"""Celery tasks for the subjective system.

Two orchestrated jobs:
  • generate_test_skills  — at test creation: extract questions+marks from the
    paper, then generate the per-question checking guides.
  • check_answer_sheet    — on student upload: quality gate → render → line-level
    extraction → question-wise reconstruction → evaluation → reviewer pass →
    annotated checked PDF. The reviewer pass is a tracked step; per-question full
    marks are a hard cap (service.clamp_marks).
"""
import io
import logging
import uuid

from workers.celery_app import celery_app
from workers.runtime import run_task

logger = logging.getLogger(__name__)


# ── Test setup: question extraction + per-question checking guides ───────────────

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
        from app.modules.subjective.models import (
            QuestionSpecificCheckingSkill, SubjectiveQuestion, SubjectiveTest,
        )
        from app.ai.agents.question_paper_agent import QuestionPaperAgent
        from app.ai.agents.checking_skill_agent import CheckingSkillAgent

        jid = uuid.UUID(job_id)
        t_r = await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == uuid.UUID(test_id)))
        test = t_r.scalar_one_or_none()
        if not test:
            raise ValueError(f"SubjectiveTest {test_id} not found")

        await update_job(db, jid, status=JobStatus.processing, progress=10, step="Reading question paper")
        paper_text = await _resolve_text(db, test.question_paper_file_id)
        if not paper_text.strip():
            raise ValueError("Could not read any text from the question paper")

        await update_job(db, jid, progress=25, step="Extracting questions and marks")
        questions = await QuestionPaperAgent(db).extract(
            paper_text=paper_text, custom_instruction=test.custom_instruction, test_id=test.id,
        )

        # Persist questions (replace any from a previous run).
        old = await db.execute(select(SubjectiveQuestion).where(SubjectiveQuestion.test_id == test.id))
        for q in old.scalars().all():
            await db.delete(q)
        await db.flush()

        question_rows: list[SubjectiveQuestion] = []
        for order, q in enumerate(questions):
            row = SubjectiveQuestion(
                test_id=test.id,
                question_number=q["question_number"],
                question_text=q["question_text"],
                marks=q["marks"],
                question_order=order,
            )
            db.add(row)
            question_rows.append(row)
        await db.flush()

        marks_sum = sum(q["marks"] for q in questions)
        test.num_questions = len(question_rows)
        if marks_sum > 0:
            test.total_marks = marks_sum
        await db.commit()

        # Inputs shared by every question's checking guide.
        model_answer = await _resolve_text(db, test.model_answer_file_id)
        rubric_text = await _resolve_text(db, test.rubric_file_id)

        agent = CheckingSkillAgent(db)
        total = len(question_rows)
        for i, q in enumerate(question_rows):
            await update_job(
                db, jid, progress=40 + int(50 * i / max(1, total)),
                step=f"Generating checking guide {i + 1}/{total}",
            )
            skill_json = await agent.generate(
                question_number=q.question_number,
                question_text=q.question_text,
                marks=q.marks,
                model_answer=model_answer,
                rubric=rubric_text,
                custom_instruction=test.custom_instruction,
                test_id=test.id,
            )
            db.add(QuestionSpecificCheckingSkill(
                test_id=test.id, question_id=q.id, skill_json=skill_json, version=1, is_active=True,
            ))
        test.skill_generation_status = "completed"
        await db.commit()

        await update_job(
            db, jid, status=JobStatus.completed, progress=100,
            step="Test ready", output={"questions": total, "total_marks": test.total_marks},
        )

    try:
        run_task(work, job_id=job_id, task=self)
    except Exception as exc:
        logger.exception("generate_test_skills failed: %s", exc)
        # Mark the test so the admin sees the failure (best-effort, separate session).
        _mark_skill_failed(test_id)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


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
        from app.processing import pdf_tools, image_quality, annotation as annotate

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
        page_pngs = [p.png_bytes for p in pages]

        # 1) Quality gate ---------------------------------------------------------
        await update_job(db, jid, progress=16, step="Checking image quality")
        metrics = image_quality.assess(page_pngs)
        db.add(AnswerQualityCheck(
            sheet_id=sheet.id,
            blur_score=metrics.blur_score,
            brightness_score=metrics.brightness_score,
            tilt_angle=metrics.tilt_angle,
            resolution_ok=metrics.resolution_ok,
            readability_score=metrics.readability_score,
            overall_status=metrics.overall_status,
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

        # 2) Full line-level extraction ------------------------------------------
        sheet.current_status = "extracting"
        await db.commit()
        await update_job(db, jid, progress=24, step="Extracting handwriting (line level)")
        valid_numbers = [q.question_number for q in questions]
        extractor = AnswerExtractionAgent(db)
        all_lines: list[dict] = []
        confidences: list[float] = []
        for idx, page in enumerate(pages):
            await update_job(
                db, jid, progress=24 + int(26 * idx / max(1, len(pages))),
                step=f"Extracting page {idx + 1}/{len(pages)}",
            )
            page_out = await extractor.extract_page(
                page_png=page.png_bytes, page_number=page.page_number,
                width=page.width, height=page.height,
                valid_numbers=valid_numbers, sheet_id=sheet.id,
            )
            all_lines.extend(page_out["lines"])
            confidences.append(page_out.get("page_confidence", 0.0))

        extraction = {
            "pages": [{"page": p.page_number, "width": p.width, "height": p.height} for p in pages],
            "lines": all_lines,
        }
        overall_conf = round(sum(confidences) / len(confidences), 3) if confidences else None
        db.add(AnswerExtraction(
            sheet_id=sheet.id, extracted_data=extraction,
            overall_confidence=overall_conf, model_used="gpt-5.5",
        ))
        await db.commit()

        # 3) Reconstruct question-wise -------------------------------------------
        await update_job(db, jid, progress=54, step="Reconstructing answers question-wise")
        reconstructed = svc.reconstruct_questionwise(extraction, questions)

        # Per-question checking guides keyed by question number.
        sk_r = await db.execute(
            select(QuestionSpecificCheckingSkill, SubjectiveQuestion.question_number)
            .join(SubjectiveQuestion, SubjectiveQuestion.id == QuestionSpecificCheckingSkill.question_id)
            .where(QuestionSpecificCheckingSkill.test_id == test.id)
        )
        skills_by_qid = {qnum: skill.skill_json for skill, qnum in sk_r.all()}

        # 4) Evaluation -----------------------------------------------------------
        sheet.current_status = "evaluating"
        await db.commit()
        await update_job(db, jid, progress=62, step="Evaluating answers")
        rubric_text = await _resolve_text(db, test.rubric_file_id) or None
        initial_eval = await AnswerEvaluationAgent(db).evaluate(
            reconstructed=reconstructed, skills_by_qid=skills_by_qid,
            rubric_text=rubric_text, custom_instruction=test.custom_instruction, sheet_id=sheet.id,
        )
        import copy
        clamped_initial, _, _ = svc.clamp_marks(copy.deepcopy(initial_eval), questions)

        # 5) Reviewer / verification pass ----------------------------------------
        sheet.current_status = "reviewing"
        await db.commit()
        await update_job(db, jid, progress=74, step="Reviewer verification pass")
        full_marks_by_qid = {q.question_number: q.marks for q in questions}
        reviewed = await AnswerReviewerAgent(db).review(
            evaluation=clamped_initial, full_marks_by_qid=full_marks_by_qid, sheet_id=sheet.id,
        )
        review_notes = reviewed.get("review_notes")
        final_eval, awarded, possible = svc.clamp_marks(reviewed, questions)

        db.add(AnswerEvaluation(
            sheet_id=sheet.id,
            evaluation_data=final_eval,
            initial_evaluation_data=initial_eval,
            reviewed=True,
            review_notes=review_notes,
            total_marks_awarded=awarded,
            total_marks_possible=possible,
            overall_confidence=overall_conf,
            model_used="gpt-5.5",
        ))
        await db.commit()

        # 6) Annotate + build checked PDF ----------------------------------------
        sheet.current_status = "annotating"
        await db.commit()
        await update_job(db, jid, progress=86, step="Drawing checked PDF")

        commands_by_page = _build_annotation_commands(
            final_eval, reconstructed, all_lines, pages, awarded, possible,
        )
        annotated_pngs = [
            annotate.draw_annotations(page.png_bytes, commands_by_page.get(page.page_number, []))
            for page in pages
        ]
        checked_pdf = pdf_tools.build_pdf_from_images(annotated_pngs)

        checked_key = f"answer-sheets/checked/{sheet.id}/checked.pdf"
        get_r2().upload_fileobj(checked_key, io.BytesIO(checked_pdf), "application/pdf")
        checked_file = File(
            original_filename="checked.pdf",
            display_name=f"{test.display_name} — Checked",
            mime_type="application/pdf",
            file_size=len(checked_pdf),
            r2_key=checked_key,
            uploaded_by=sheet.student_id,
        )
        db.add(checked_file)
        await db.flush()
        db.add(PDFAnnotation(
            sheet_id=sheet.id,
            annotation_instructions={"pages": {str(k): v for k, v in commands_by_page.items()}},
            checked_file_id=checked_file.id,
            annotation_status="completed",
        ))
        sheet.current_status = "checked"
        await db.commit()

        await update_job(
            db, jid, status=JobStatus.completed, progress=100, step="Checked",
            output={
                "total_marks_awarded": awarded,
                "total_marks_possible": possible,
                "quality_status": metrics.overall_status,
            },
        )

    try:
        run_task(work, job_id=job_id, task=self)
    except Exception as exc:
        logger.exception("check_answer_sheet failed: %s", exc)
        _mark_sheet_failed(sheet_id)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


# ── helpers ─────────────────────────────────────────────────────────────────────

async def _resolve_text(db, file_id) -> str:
    """Best-effort text for a stored file: direct text extraction, with a vision
    OCR fallback for scanned PDFs / images that carry no embedded text."""
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

    # Vision OCR fallback (scanned paper or image upload).
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


def _build_annotation_commands(
    evaluation: dict, reconstructed: dict, all_lines: list[dict], pages: list, awarded: float, possible: float,
) -> dict[int, list[dict]]:
    """Turn the reviewed evaluation into per-page draw commands.

    - underline/circle: only for AI-flagged specific wrong lines (resolved via line id → bbox)
    - mark: each question's "m/fm" placed near that question's first answer line
    - banner: total marks on page 1
    Keeps it sparse to avoid overcrowding (CLAUDE.md §12).
    """
    line_index = {ln["id"]: ln for ln in all_lines if ln.get("id")}
    page_width = {p.page_number: p.width for p in pages}
    first_page = pages[0].page_number if pages else 1

    # First answer line (with bbox) per question, for placing the mark.
    first_line_by_q: dict[str, dict] = {}
    for q in reconstructed.get("questions", []):
        for ln in q.get("lines", []):
            if ln.get("bbox"):
                first_line_by_q[q["qid"]] = ln
                break

    commands: dict[int, list[dict]] = {}

    def add(page_no: int, cmd: dict) -> None:
        commands.setdefault(page_no, []).append(cmd)

    for item in evaluation.get("questions", []):
        qid = str(item.get("qid") or "")
        # Line annotations for specific wrong items only.
        for ann in item.get("ann", []) or []:
            if not isinstance(ann, dict):
                continue
            t = ann.get("t")
            line_id = ann.get("line")
            if t in ("underline", "circle") and line_id and line_id in line_index:
                ref = line_index[line_id]
                if ref.get("bbox"):
                    add(ref.get("page", first_page), {"type": t, "bbox": ref["bbox"]})
            elif t == "comment" and ann.get("text"):
                anchor = first_line_by_q.get(qid)
                page_no = anchor.get("page", first_page) if anchor else first_page
                y = anchor["bbox"][1] if (anchor and anchor.get("bbox")) else 20
                add(page_no, {"type": "comment", "text": str(ann["text"]),
                              "x": int(page_width.get(page_no, 1000) * 0.7), "y": int(y)})

        # Per-question mark near the question's first line.
        anchor = first_line_by_q.get(qid)
        if anchor and anchor.get("bbox"):
            page_no = anchor.get("page", first_page)
            bx, by, bw, bh = anchor["bbox"]
            mark_text = f"{_fmt(item.get('m', 0))}/{_fmt(item.get('fm', 0))}"
            add(page_no, {"type": "mark", "text": mark_text,
                          "x": min(int(page_width.get(page_no, 1000) * 0.86), bx + bw + 10), "y": by})

    add(first_page, {"type": "banner", "text": f"Total: {_fmt(awarded)} / {_fmt(possible)}"})
    return commands


def _fmt(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else str(round(f, 1))


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
