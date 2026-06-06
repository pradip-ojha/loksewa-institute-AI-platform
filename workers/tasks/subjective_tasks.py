"""Celery tasks for the subjective system.

Two orchestrated jobs:
  • generate_test_skills  — at test creation (and on regenerate): extract questions +
    marks from the paper, detect each question's topic/subtopic, fetch supporting
    knowledge (best-effort), then run the multi-agent skill generation:
    SkillGenerator → SkillEvaluator → improve weak skills once (max 2 iterations) →
    lock the per-question examiner skills. All heavy resource reading happens HERE.
  • check_answer_sheet    — on student upload: quality gate → question-level extraction
    → checker (locked skills + test config only, NO big notes) → reviewer pass →
    per-target vision locator → geometry validator → human-like checked PDF. The
    reviewer pass is a tracked step; per-question full marks are a hard cap
    (service.clamp_marks).
"""
import copy
import io
import logging
import uuid

from workers.celery_app import celery_app
from workers.runtime import run_task

logger = logging.getLogger(__name__)

# Keep the checked PDF uncrowded (CLAUDE.md §12).
MAX_TARGETS_PER_QUESTION = 3
MAX_TARGETS_PER_PAGE = 6


# ── Test setup: question extraction + multi-agent skill generation ───────────────

@celery_app.task(
    bind=True,
    name="workers.tasks.subjective_tasks.generate_test_skills",
    max_retries=1,
    default_retry_delay=30,
)
def generate_test_skills(self, job_id: str, test_id: str) -> None:
    async def work(db) -> None:
        from sqlalchemy import select
        from app.modules.jobs.models import JobStatus
        from app.modules.jobs.service import update_job
        from app.modules.subjective import service as svc
        from app.modules.subjective.models import (
            QuestionSpecificCheckingSkill, SubjectiveQuestion, SubjectiveTest,
        )
        from app.modules.video.service import get_chapter_tree
        from app.ai.agents.question_paper_agent import QuestionPaperAgent
        from app.ai.agents.subjective_topic_router_agent import SubjectiveTopicRouterAgent
        from app.ai.agents.skill_generator_agent import SkillGeneratorAgent
        from app.ai.agents.skill_evaluator_agent import SkillEvaluatorAgent

        jid = uuid.UUID(job_id)
        t_r = await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == uuid.UUID(test_id)))
        test = t_r.scalar_one_or_none()
        if not test:
            raise ValueError(f"SubjectiveTest {test_id} not found")

        await update_job(db, jid, status=JobStatus.processing, progress=8, step="Reading question paper")
        paper_text = await _resolve_text(db, test.question_paper_file_id)
        if not paper_text.strip():
            raise ValueError("Could not read any text from the question paper")

        await update_job(db, jid, progress=18, step="Extracting questions and marks")
        questions = await QuestionPaperAgent(db).extract(
            paper_text=paper_text, custom_instruction=test.custom_instruction, test_id=test.id,
        )

        # Persist questions (replace any from a previous run — cascades old skills).
        old = await db.execute(select(SubjectiveQuestion).where(SubjectiveQuestion.test_id == test.id))
        for q in old.scalars().all():
            await db.delete(q)
        await db.flush()

        question_rows: list[SubjectiveQuestion] = []
        for order, q in enumerate(questions):
            row = SubjectiveQuestion(
                test_id=test.id, question_number=q["question_number"],
                question_text=q["question_text"], marks=q["marks"], question_order=order,
            )
            db.add(row)
            question_rows.append(row)
        await db.flush()

        marks_sum = sum(q["marks"] for q in questions)
        test.num_questions = len(question_rows)
        if marks_sum > 0:
            test.total_marks = marks_sum
        await db.commit()

        # Detect topic/subtopic per question against the subjective syllabus tree.
        await update_job(db, jid, progress=30, step="Detecting topics")
        tree_text, valid_topics, valid_subtopics = await get_chapter_tree(db, "subjective")
        router = SubjectiveTopicRouterAgent(db)
        for q in question_rows:
            try:
                route = await router.route(
                    question_number=q.question_number, question_text=q.question_text,
                    tree_text=tree_text, test_id=test.id,
                )
                q.topic = route["topic"] if route["topic"] in valid_topics else None
                q.subtopic = route["subtopic"] if route["subtopic"] in valid_subtopics else None
            except Exception as exc:
                logger.warning("topic routing failed for %s: %s", q.question_number, exc)
        await db.commit()

        # Shared inputs distilled into the skills (read ONCE, here).
        model_answer = await _resolve_text(db, test.model_answer_file_id)
        rubric_text = await _resolve_text(db, test.rubric_file_id)

        generator = SkillGeneratorAgent(db)
        total = len(question_rows)
        skills: list[dict] = []   # working list: {q, skill_json}
        for i, q in enumerate(question_rows):
            await update_job(
                db, jid, progress=36 + int(34 * i / max(1, total)),
                step=f"Generating checking skill {i + 1}/{total}",
            )
            knowledge = await svc.fetch_question_resources(
                db, topic=q.topic, subtopic=q.subtopic, query=q.question_text,
            )
            skill_json = await generator.generate(
                question_number=q.question_number, question_text=q.question_text, marks=q.marks,
                topic=q.topic, subtopic=q.subtopic, model_answer=model_answer, rubric=rubric_text,
                custom_instruction=test.custom_instruction, knowledge=knowledge, test_id=test.id,
            )
            skills.append({"q": q, "skill_json": skill_json, "knowledge": knowledge, "iterations": 1,
                           "evaluation_status": "passed", "evaluation_notes": None})

        # Evaluate the generated skills (lenient gate), then improve only the weak ones.
        await update_job(db, jid, progress=74, step="Evaluating checking skills")
        verdicts = await _evaluate_skills(db, SkillEvaluatorAgent(db), skills, test, svc)
        weak = [s for s in skills if verdicts.get(s["q"].question_number, {}).get("status") == "failed"]
        if weak:
            await update_job(db, jid, progress=84, step=f"Improving {len(weak)} weak skill(s)")
            for s in weak:
                q = s["q"]
                fb = verdicts.get(q.question_number, {})
                feedback = " ".join(filter(None, [
                    "; ".join(fb.get("issues") or []), fb.get("fix_feedback") or "",
                ])) or "Make the guide more usable and correctly mapped."
                try:
                    s["skill_json"] = await generator.improve(
                        question_number=q.question_number, question_text=q.question_text, marks=q.marks,
                        topic=q.topic, subtopic=q.subtopic, model_answer=model_answer, rubric=rubric_text,
                        custom_instruction=test.custom_instruction, knowledge=s["knowledge"],
                        previous_skill=s["skill_json"], evaluator_feedback=feedback, test_id=test.id,
                    )
                    s["iterations"] = 2
                except Exception as exc:
                    logger.warning("skill improve failed for %s: %s", q.question_number, exc)
            verdicts2 = await _evaluate_skills(db, SkillEvaluatorAgent(db), weak, test, svc)
            verdicts.update(verdicts2)

        # Lock the skills (no admin gate). Residual failure → passed_with_warning (audit).
        await update_job(db, jid, progress=92, step="Locking checking skills")
        for s in skills:
            q = s["q"]
            v = verdicts.get(q.question_number, {})
            status = v.get("status") or "passed"
            if status == "failed":
                status = "passed_with_warning"
            notes = " ".join(filter(None, [
                "; ".join(v.get("issues") or []), v.get("fix_feedback") or "",
            ])) or None
            db.add(QuestionSpecificCheckingSkill(
                test_id=test.id, question_id=q.id, skill_json=s["skill_json"], version=1, is_active=True,
                evaluation_status=status, evaluation_notes=notes, iterations=s["iterations"],
            ))
        test.skill_generation_status = "completed"
        await db.commit()

        await update_job(
            db, jid, status=JobStatus.completed, progress=100, step="Test ready",
            output={"questions": total, "total_marks": test.total_marks, "improved": len(weak)},
        )

    try:
        run_task(work, job_id=job_id, task=self)
    except Exception as exc:
        logger.exception("generate_test_skills failed: %s", exc)
        _mark_skill_failed(test_id)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


async def _evaluate_skills(db, evaluator, skills: list[dict], test, svc) -> dict[str, dict]:
    """Run the SkillEvaluator over `skills` and return {question_number: verdict}.
    Best-effort: on evaluator failure, treat all as passed (never block the demo)."""
    payload = [{
        "question_number": s["q"].question_number, "marks": s["q"].marks,
        "question_text": s["q"].question_text, "topic": s["q"].topic, "subtopic": s["q"].subtopic,
        "skill_json": s["skill_json"],
    } for s in skills]
    valid = [s["q"].question_number for s in skills]
    try:
        result = await evaluator.evaluate(
            skills=payload, custom_instruction=test.custom_instruction, test_id=test.id,
        )
    except Exception as exc:
        logger.warning("skill evaluation failed (locking as-is): %s", exc)
        return {}
    out: dict[str, dict] = {}
    for r in result.get("results", []):
        qnum = svc._match_question_number(str(r.get("question_number") or ""), valid)
        if qnum:
            out[qnum] = {
                "status": (r.get("status") or "passed"),
                "issues": r.get("issues") or [],
                "fix_feedback": r.get("fix_feedback") or "",
            }
    return out


# ── Answer-sheet checking pipeline ──────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="workers.tasks.subjective_tasks.check_answer_sheet",
    max_retries=1,
    default_retry_delay=30,
)
def check_answer_sheet(self, job_id: str, sheet_id: str) -> None:
    async def work(db) -> None:
        from sqlalchemy import select
        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from app.modules.jobs.models import JobStatus
        from app.modules.jobs.service import update_job
        from app.modules.subjective import service as svc
        from app.modules.subjective.models import (
            AnswerEvaluation, AnswerExtraction, AnswerQualityCheck, PDFAnnotation,
            QuestionSpecificCheckingSkill, StudentAnswerSheet, SubjectiveQuestion, SubjectiveTest,
        )
        from app.ai.agents.answer_extraction_agent import AnswerExtractionAgent
        from app.ai.agents.answer_evaluation_agent import AnswerEvaluationAgent
        from app.ai.agents.answer_reviewer_agent import AnswerReviewerAgent
        from app.ai.agents.annotation_locator_agent import AnnotationLocatorAgent
        from app.processing import pdf_tools, image_quality, annotation as annotate
        from app.processing import annotation_geometry as geom

        jid = uuid.UUID(job_id)
        sid = uuid.UUID(sheet_id)

        s_r = await db.execute(select(StudentAnswerSheet).where(StudentAnswerSheet.id == sid))
        sheet = s_r.scalar_one_or_none()
        if not sheet:
            raise ValueError(f"StudentAnswerSheet {sheet_id} not found")
        test = (await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == sheet.test_id))).scalar_one()
        questions = await svc.get_test_questions(db, test.id)

        f_r = await db.execute(select(File).where(File.id == sheet.file_id))
        file_record = f_r.scalar_one_or_none()
        if not file_record:
            raise ValueError("Answer-sheet file not found")

        await update_job(db, jid, status=JobStatus.processing, progress=8, step="Rendering pages")
        file_bytes = get_r2().download_fileobj(file_record.r2_key)
        pages = pdf_tools.render_to_page_images(file_bytes, file_record.mime_type)
        page_map = {p.page_number: p for p in pages}
        page_pngs = [p.png_bytes for p in pages]

        # 1) Quality gate ---------------------------------------------------------
        await update_job(db, jid, progress=15, step="Checking image quality")
        metrics = image_quality.assess(page_pngs)
        db.add(AnswerQualityCheck(
            sheet_id=sheet.id, blur_score=metrics.blur_score, brightness_score=metrics.brightness_score,
            tilt_angle=metrics.tilt_angle, resolution_ok=metrics.resolution_ok,
            readability_score=metrics.readability_score, overall_status=metrics.overall_status,
            quality_notes=metrics.quality_notes,
        ))
        if metrics.overall_status == "poor" and sheet.upload_attempt_number < svc.MAX_UPLOAD_ATTEMPTS:
            sheet.current_status = "needs_reupload"
            await db.commit()
            await update_job(
                db, jid, status=JobStatus.completed, progress=100, step="Re-upload requested",
                output={"needs_reupload": True, "quality_notes": metrics.quality_notes},
            )
            return
        await db.commit()

        # 2) Question-level extraction -------------------------------------------
        sheet.current_status = "extracting"
        await db.commit()
        await update_job(db, jid, progress=24, step="Extracting answers (question level)")
        valid_numbers = [q.question_number for q in questions]
        extractor = AnswerExtractionAgent(db)
        page_outputs: list[dict] = []
        confidences: list[float] = []
        for idx, page in enumerate(pages):
            await update_job(
                db, jid, progress=24 + int(24 * idx / max(1, len(pages))),
                step=f"Extracting page {idx + 1}/{len(pages)}",
            )
            po = await extractor.extract_page(
                page_png=page.png_bytes, page_number=page.page_number,
                width=page.width, height=page.height,
                valid_numbers=valid_numbers, sheet_id=sheet.id,
            )
            page_outputs.append(po)
            confidences.append(po.get("page_confidence", 0.0))

        extraction = svc.assemble_questionwise(page_outputs, questions)
        overall_conf = round(sum(confidences) / len(confidences), 3) if confidences else None
        db.add(AnswerExtraction(
            sheet_id=sheet.id, extracted_data=extraction,
            overall_confidence=overall_conf, model_used="gpt-5.5",
        ))
        await db.commit()

        # Locked per-question checking skills.
        sk_r = await db.execute(
            select(QuestionSpecificCheckingSkill, SubjectiveQuestion.question_number)
            .join(SubjectiveQuestion, SubjectiveQuestion.id == QuestionSpecificCheckingSkill.question_id)
            .where(QuestionSpecificCheckingSkill.test_id == test.id)
        )
        skills_by_qid = {qnum: skill.skill_json for skill, qnum in sk_r.all()}

        # 3) Checker --------------------------------------------------------------
        sheet.current_status = "evaluating"
        await db.commit()
        await update_job(db, jid, progress=54, step="Checking answers")
        rubric_text = await _resolve_text(db, test.rubric_file_id) or None
        checker_questions = [{
            "qid": q["qid"], "question_text": q["question_text"], "marks": q["marks"],
            "answer_text": q["answer_text"], "page_numbers": q["page_numbers"],
        } for q in extraction["questions"]]
        initial_eval = await AnswerEvaluationAgent(db).evaluate(
            questions=checker_questions, skills_by_qid=skills_by_qid,
            rubric_text=rubric_text, custom_instruction=test.custom_instruction, sheet_id=sheet.id,
        )
        clamped_initial, _, _ = svc.clamp_marks(copy.deepcopy(initial_eval), questions)

        # 4) Reviewer / verification pass ----------------------------------------
        sheet.current_status = "reviewing"
        await db.commit()
        await update_job(db, jid, progress=66, step="Reviewer verification pass")
        full_marks_by_qid = {q.question_number: q.marks for q in questions}
        reviewed = await AnswerReviewerAgent(db).review(
            evaluation=clamped_initial, full_marks_by_qid=full_marks_by_qid, sheet_id=sheet.id,
        )
        review_notes = reviewed.get("review_notes")
        final_eval, awarded, possible = svc.clamp_marks(reviewed, questions)

        db.add(AnswerEvaluation(
            sheet_id=sheet.id, evaluation_data=final_eval, initial_evaluation_data=initial_eval,
            reviewed=True, review_notes=review_notes, total_marks_awarded=awarded,
            total_marks_possible=possible, overall_confidence=overall_conf, model_used="gpt-5.5",
        ))
        await db.commit()

        # 5) Locate + validate annotation geometry -------------------------------
        sheet.current_status = "annotating"
        await db.commit()
        await update_job(db, jid, progress=78, step="Locating annotations")
        locator = AnnotationLocatorAgent(db)
        regions_by_q = _regions_by_question(extraction)
        commands_by_page, locator_plans = await _locate_and_build_commands(
            final_eval, regions_by_q, page_map, locator, geom, sheet.id,
        )

        await update_job(db, jid, progress=90, step="Drawing checked PDF")
        # Per-question marks + total banner.
        _add_marks_and_banner(commands_by_page, final_eval, regions_by_q, page_map, awarded, possible)

        annotated_pngs = [
            annotate.draw_annotations(page.png_bytes, commands_by_page.get(page.page_number, []))
            for page in pages
        ]
        checked_pdf = pdf_tools.build_pdf_from_images(annotated_pngs)

        checked_key = f"answer-sheets/checked/{sheet.id}/checked.pdf"
        get_r2().upload_fileobj(checked_key, io.BytesIO(checked_pdf), "application/pdf")
        checked_file = File(
            original_filename="checked.pdf", display_name=f"{test.display_name} — Checked",
            mime_type="application/pdf", file_size=len(checked_pdf), r2_key=checked_key,
            uploaded_by=sheet.student_id,
        )
        db.add(checked_file)
        await db.flush()
        db.add(PDFAnnotation(
            sheet_id=sheet.id,
            annotation_instructions={"pages": {str(k): v for k, v in commands_by_page.items()}},
            locator_plan={"targets": locator_plans},
            checked_file_id=checked_file.id, annotation_status="completed",
        ))
        sheet.current_status = "checked"
        await db.commit()

        await update_job(
            db, jid, status=JobStatus.completed, progress=100, step="Checked",
            output={"total_marks_awarded": awarded, "total_marks_possible": possible,
                    "quality_status": metrics.overall_status},
        )

    try:
        run_task(work, job_id=job_id, task=self)
    except Exception as exc:
        logger.exception("check_answer_sheet failed: %s", exc)
        _mark_sheet_failed(sheet_id)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


# ── annotation helpers ───────────────────────────────────────────────────────────

def _regions_by_question(extraction: dict) -> dict[str, list[dict]]:
    """{question_number: [{page, question_bbox}]} from the stored extraction."""
    out: dict[str, list[dict]] = {}
    for q in extraction.get("questions", []):
        out[str(q.get("qid"))] = q.get("page_regions") or []
    return out


async def _locate_and_build_commands(final_eval, regions_by_q, page_map, locator, geom, sheet_id):
    """For each reviewed annotation target: run the vision locator, validate the
    geometry, and emit underline/comment draw commands. Returns (commands_by_page,
    locator_plans) — plans are persisted for audit. Stays uncrowded via per-page and
    per-question caps."""
    commands_by_page: dict[int, list[dict]] = {}
    locator_plans: list[dict] = []
    page_target_count: dict[int, int] = {}

    def add(page_no: int, cmd: dict) -> None:
        commands_by_page.setdefault(page_no, []).append(cmd)

    for qres in final_eval.get("question_results", []):
        qnum = str(qres.get("question_number") or "")
        regions = regions_by_q.get(qnum, [])
        targets = (qres.get("annotation_targets") or [])[:MAX_TARGETS_PER_QUESTION]
        for target in targets:
            if not isinstance(target, dict) or not (target.get("target_text") or "").strip():
                continue
            page_no = target.get("page_number") or (regions[0]["page"] if regions else None)
            page = page_map.get(page_no)
            if not page:
                continue
            if page_target_count.get(page_no, 0) >= MAX_TARGETS_PER_PAGE:
                continue
            qbbox = next((r.get("question_bbox") for r in regions if r.get("page") == page_no), None)
            try:
                loc = await locator.locate(
                    page_png=page.png_bytes, page_number=page_no, width=page.width, height=page.height,
                    question_number=qnum, target_text=target.get("target_text", ""),
                    comment_text=target.get("comment_text", ""),
                    annotation_action=target.get("annotation_action", "underline_with_comment"),
                    question_bbox=qbbox, sheet_id=sheet_id,
                )
            except Exception as exc:
                logger.warning("locator failed (skipping target): %s", exc)
                continue
            plan = geom.validate_and_smooth(loc, (page.width, page.height), qbbox)
            locator_plans.append({k: v for k, v in plan.items() if k != "original"})
            page_target_count[page_no] = page_target_count.get(page_no, 0) + 1

            if plan.get("final_underline_paths"):
                add(page_no, {"type": "underline_path", "paths": plan["final_underline_paths"]})
            if plan.get("final_comment_box") and (plan.get("comment_text") or "").strip():
                add(page_no, {"type": "comment", "text": plan["comment_text"], "box": plan["final_comment_box"]})
    return commands_by_page, locator_plans


def _add_marks_and_banner(commands_by_page, final_eval, regions_by_q, page_map, awarded, possible) -> None:
    first_page = min(page_map.keys()) if page_map else 1

    def add(page_no: int, cmd: dict) -> None:
        commands_by_page.setdefault(page_no, []).append(cmd)

    for qres in final_eval.get("question_results", []):
        qnum = str(qres.get("question_number") or "")
        regions = [r for r in regions_by_q.get(qnum, []) if r.get("question_bbox")]
        if not regions:
            continue
        # Place the mark at the END of the answer (bottom-right of the largest region),
        # like a teacher's tick — never the page-top corner.
        region = max(regions, key=lambda r: (r["question_bbox"][2] * r["question_bbox"][3]))
        page = page_map.get(region.get("page"))
        if not page:
            continue
        x, y, w, h = region["question_bbox"]
        mark_text = f"{_fmt(qres.get('awarded_marks', 0))}/{_fmt(qres.get('max_marks', 0))}"
        mx = min(int(page.width * 0.88), int(x + w - 10))
        my = int(y + h - 6)
        if region["page"] == first_page and my < 90:   # don't collide with the total banner
            my = 96
        add(region["page"], {"type": "mark", "text": mark_text, "x": mx, "y": my})
    add(first_page, {"type": "banner", "text": f"Total: {_fmt(awarded)} / {_fmt(possible)}"})


def _fmt(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else str(round(f, 1))


# ── text-resolution + failure helpers ────────────────────────────────────────────

async def _resolve_text(db, file_id) -> str:
    """Best-effort text for a stored file: direct text extraction, with a vision OCR
    fallback for scanned PDFs / images that carry no embedded text."""
    if not file_id:
        return ""
    from sqlalchemy import select
    from app.integrations.r2_client import get_r2
    from app.modules.files.models import File
    from app.processing.document_text import extract_text_from_bytes

    f_r = await db.execute(select(File).where(File.id == file_id))
    f = f_r.scalar_one_or_none()
    if not f:
        return ""
    data = get_r2().download_fileobj(f.r2_key)

    text = ""
    if f.mime_type in (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        try:
            text = extract_text_from_bytes(data, f.mime_type)
        except Exception:
            text = ""
    if len(text.strip()) >= 30:
        return text
    return await _vision_ocr(db, data, f.mime_type, file_id)


async def _vision_ocr(db, data: bytes, mime_type: str, entity_id) -> str:
    from app.ai.model_router import get_provider
    from app.processing import pdf_tools

    try:
        pages = pdf_tools.render_to_page_images(data, mime_type)
    except Exception:
        return ""
    provider = get_provider("reasoning")
    parts: list[str] = []
    for page in pages[:15]:
        try:
            res = await provider.generate_with_image(
                "Transcribe ALL printed/handwritten text on this page exactly, preserving Devanagari. "
                "Return JSON: {\"text\": \"...\"}.",
                page.png_bytes, schema={},
                audit_ctx={"db": db, "agent_type": "QuestionPaperAgent", "task_type": "paper_vision_ocr",
                           "entity_type": "file", "entity_id": entity_id},
            )
            if isinstance(res, dict) and res.get("text"):
                parts.append(str(res["text"]))
        except Exception as exc:
            logger.warning("vision OCR failed on a page: %s", exc)
    return "\n".join(parts)


def _mark_skill_failed(test_id: str) -> None:
    from workers.runtime import run_async

    async def _do():
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal
        from app.modules.subjective.models import SubjectiveTest
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == uuid.UUID(test_id)))
                t = r.scalar_one_or_none()
                if t:
                    t.skill_generation_status = "failed"
                    await db.commit()
        except Exception:
            logger.exception("Could not mark test %s skill generation failed", test_id)

    try:
        run_async(_do())
    except Exception:
        logger.exception("mark_skill_failed wrapper failed")


def _mark_sheet_failed(sheet_id: str) -> None:
    from workers.runtime import run_async

    async def _do():
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal
        from app.modules.subjective.models import StudentAnswerSheet
        try:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(StudentAnswerSheet).where(StudentAnswerSheet.id == uuid.UUID(sheet_id)))
                s = r.scalar_one_or_none()
                if s:
                    s.current_status = "failed"
                    await db.commit()
        except Exception:
            logger.exception("Could not mark sheet %s failed", sheet_id)

    try:
        run_async(_do())
    except Exception:
        logger.exception("mark_sheet_failed wrapper failed")
