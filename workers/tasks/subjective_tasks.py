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
import asyncio
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
# How many per-(question,page) vision locator calls run concurrently. Each call also
# borrows one short-lived DB session for audit, so keep this within DB pool headroom.
LOCATOR_CONCURRENCY = 6
# The annotation phase runs AFTER marks are final and the sheet is already
# `feedback_ready`. Bound it well under the task hard-timeout so a slow ~40-call
# locator loop on a large sheet settles to `checked` (results stand, PDF unavailable)
# instead of letting the OUTER task timeout fire mid-annotation and fail the task.
ANNOTATION_BUDGET_SECONDS = 600


class MarksReconciliationError(Exception):
    """The extracted per-question marks could not be reconciled to the admin-configured
    Total Marks after every attempt. Terminal + NON-transient: a Celery retry would only
    re-read the same paper, so `generate_test_skills` fails the job cleanly (with a clear
    admin message) instead of retrying."""


# ── Test setup: question extraction + multi-agent skill generation ───────────────

@celery_app.task(
    bind=True,
    name="workers.tasks.subjective_tasks.generate_test_skills",
    max_retries=1,
    default_retry_delay=30,
)
def generate_test_skills(self, job_id: str, test_id: str) -> None:
    # Borrow-per-use session model (run_task manage_session=False): topic routing (~2 min)
    # and the per-question knowledge-fetch + skill generation (~5-10 min) run as long
    # parallel phases. A single held session would sit idle across them and be dropped
    # server-side. `work()` takes NO long-lived session — every DB touch borrows a
    # short-lived one (LOAD → WORK → SAVE), so no connection is ever held idle.
    async def work() -> None:
        import asyncio
        from sqlalchemy import select
        from app.core.database import AsyncSessionLocal
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

        jid = uuid.UUID(job_id)
        test_uuid = uuid.UUID(test_id)

        async def _job(**kw) -> None:
            async with AsyncSessionLocal() as db:
                await update_job(db, jid, **kw)

        # ── LOAD: snapshot the test's scalar config, then release the connection ─
        async with AsyncSessionLocal() as db:
            test = (await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == test_uuid))).scalar_one_or_none()
            if not test:
                raise ValueError(f"SubjectiveTest {test_id} not found")
            exam_id = test.exam_id
            custom_instruction = test.custom_instruction
            question_paper_file_id = test.question_paper_file_id
            model_answer_file_id = test.model_answer_file_id
            model_answer_is_handwritten = test.model_answer_is_handwritten
            rubric_file_id = test.rubric_file_id
            # Admin-configured Total Marks: authoritative AND the reconciliation checksum
            # (0/blank ⇒ admin left it out ⇒ no checksum, fall back to the extracted sum).
            admin_total = test.total_marks or 0
            await update_job(db, jid, status=JobStatus.processing, progress=8, step="Reading question paper")

        # Resolve the paper source ONCE: a usable embedded text layer → text extraction;
        # else render pages to images for DIRECT vision extraction (no lossy OCR→text→extract
        # double pass — wording stays verbatim, marks read from the printed digit). ────
        async with AsyncSessionLocal() as db:
            source_kind, source = await _resolve_paper_source(db, question_paper_file_id)
        if source_kind == "text" and not source.strip():
            raise ValueError("Could not read any text from the question paper")
        if source_kind == "images" and not source:
            raise ValueError("Could not render the question paper for reading")

        # Extract questions + marks, RECONCILED against the admin Total Marks. Admin's total
        # is authoritative and is the checksum: if the extracted per-question marks don't sum
        # to it, re-read the paper (a fresh vision read can fix a misread digit or a missed
        # question). After MAX attempts still off ⇒ fail with a clear message rather than ship
        # a test whose per-question hard-caps are wrong. admin_total<=0 ⇒ no checksum. ──
        await _job(progress=18, step="Extracting questions and marks")
        MAX_EXTRACT_ATTEMPTS = 3
        questions: list[dict] | None = None
        best: list[dict] | None = None
        best_sum = 0
        for attempt in range(1, MAX_EXTRACT_ATTEMPTS + 1):
            async with AsyncSessionLocal() as db:
                agent = QuestionPaperAgent(db)
                if source_kind == "text":
                    cand = await agent.extract(
                        paper_text=source, custom_instruction=custom_instruction,
                        test_id=test_uuid, expected_total_marks=admin_total or None,
                    )
                else:
                    cand = await agent.extract_from_images(
                        source, custom_instruction=custom_instruction,
                        test_id=test_uuid, expected_total_marks=admin_total or None,
                    )
            cand_sum = sum(q["marks"] for q in cand)
            if admin_total <= 0 or cand_sum == admin_total:
                questions = cand  # no checksum, or reconciled exactly
                break
            if best is None or abs(cand_sum - admin_total) < abs(best_sum - admin_total):
                best, best_sum = cand, cand_sum
            logger.warning(
                "marks reconciliation attempt %d/%d: extracted sum %d != configured total %d",
                attempt, MAX_EXTRACT_ATTEMPTS, cand_sum, admin_total,
            )

        if questions is None:
            # admin_total>0 and never reconciled → terminal, NON-transient failure. run_task
            # records the job failed with this message; the outer handler does NOT celery-retry.
            raise MarksReconciliationError(
                f"Extracted per-question marks sum to {best_sum} but the configured Total Marks is "
                f"{admin_total}. Re-read the paper {MAX_EXTRACT_ATTEMPTS} times. Please verify the "
                f"question paper's marks or the Total Marks you entered, then regenerate."
            )

        # SAVE: replace any prior questions (cascades old skills), insert new rows ─
        marks_sum = sum(q["marks"] for q in questions)
        async with AsyncSessionLocal() as db:
            old = await db.execute(select(SubjectiveQuestion).where(SubjectiveQuestion.test_id == test_uuid))
            for q in old.scalars().all():
                await db.delete(q)
            await db.flush()
            question_rows: list[SubjectiveQuestion] = []
            for order, q in enumerate(questions):
                row = SubjectiveQuestion(
                    test_id=test_uuid, question_number=q["question_number"],
                    question_text=q["question_text"], marks=q["marks"], question_order=order,
                )
                db.add(row)
                question_rows.append(row)
            t = (await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == test_uuid))).scalar_one()
            t.num_questions = len(question_rows)
            # Admin's Total Marks is authoritative when set; only fall back to the extracted
            # sum when the admin left it blank (0). Reconciliation above guarantees the
            # per-question marks sum to admin_total when it is set.
            if admin_total > 0:
                t.total_marks = admin_total
            elif marks_sum > 0:
                t.total_marks = marks_sum
            total_marks = t.total_marks
            await db.commit()
            # Snapshot the question rows as plain data for WORK (ids assigned by flush;
            # scalar columns stay readable after detach, but plain dicts are clearer).
            qdata = [{"id": r.id, "question_number": r.question_number,
                      "question_text": r.question_text, "marks": r.marks,
                      "topic": None, "subtopic": None, "chapter": None} for r in question_rows]

        total = len(qdata)

        # Detect topic/subtopic per question (own session for the syllabus tree),
        # then route all questions CONCURRENTLY, each on its own short-lived session. ─
        await _job(progress=30, step="Detecting topics")
        async with AsyncSessionLocal() as db:
            tree_text, valid_topics, valid_subtopics, valid_chapters, topic_to_chapter = await get_chapter_tree(db, exam_id)

        route_sem = asyncio.Semaphore(6)

        async def _route_one(qd):
            async with route_sem:
                try:
                    async with AsyncSessionLocal() as rdb:
                        return qd, await SubjectiveTopicRouterAgent(rdb).route(
                            question_number=qd["question_number"], question_text=qd["question_text"],
                            tree_text=tree_text, test_id=test_uuid,
                        )
                except Exception as exc:
                    logger.warning("topic routing failed for %s: %s", qd["question_number"], exc)
                    return qd, None

        for qd, route in await asyncio.gather(*[_route_one(qd) for qd in qdata]):
            if route:
                qd["topic"] = route["topic"] if route["topic"] in valid_topics else None
                qd["subtopic"] = route["subtopic"] if route["subtopic"] in valid_subtopics else None
                # Chapter (PRIMARY retrieval dimension) resolved deterministically from the
                # validated topic; the router's explicit chapter is the fallback when no topic.
                route_chapter = route.get("chapter")
                qd["chapter"] = (
                    topic_to_chapter.get(qd["topic"]) if qd["topic"]
                    else (route_chapter if route_chapter in valid_chapters else None)
                )

        # SAVE: persist routing results back onto the question rows ───────────────
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(SubjectiveQuestion).where(SubjectiveQuestion.test_id == test_uuid))).scalars().all()
            by_num = {r.question_number: r for r in rows}
            for qd in qdata:
                r = by_num.get(qd["question_number"])
                if r:
                    r.topic, r.subtopic, r.chapter = qd["topic"], qd["subtopic"], qd["chapter"]
            await db.commit()

        # Shared inputs distilled into the skills (read ONCE, here, own sessions). The
        # model answer may be handwritten (→ Gemini); rubric is typed (→ gpt-5 vision). ─
        async with AsyncSessionLocal() as db:
            model_answer = await _resolve_text(
                db, model_answer_file_id, handwritten=model_answer_is_handwritten,
            )
        async with AsyncSessionLocal() as db:
            rubric_text = await _resolve_text(db, rubric_file_id)

        await _job(progress=40, step=f"Generating {total} checking skills in parallel")

        # Each question's grouped knowledge fetch + skill generation runs CONCURRENTLY
        # on its own short-lived session (audit logging makes one AsyncSession unsafe
        # to share, and borrow-per-use keeps no connection idle across the AI calls). ─
        gen_sem = asyncio.Semaphore(6)

        async def _gen_one(qd) -> dict | None:
            # Per-question fault isolation: one question's transient AI failure must NOT
            # discard every other question's completed skill (matches _route_one/_extract_one).
            async with gen_sem:
                try:
                    async with AsyncSessionLocal() as gdb:
                        knowledge = await svc.fetch_question_resources(
                            gdb, exam_id=exam_id, chapter=qd["chapter"], topic=qd["topic"],
                            subtopic=qd["subtopic"], query=qd["question_text"],
                        )
                        skill_json = await SkillGeneratorAgent(gdb).generate(
                            question_number=qd["question_number"], question_text=qd["question_text"], marks=qd["marks"],
                            topic=qd["topic"], subtopic=qd["subtopic"], model_answer=model_answer, rubric=rubric_text,
                            custom_instruction=custom_instruction, knowledge=knowledge, test_id=test_uuid,
                        )
                    return {"q": qd, "skill_json": skill_json, "knowledge": knowledge, "iterations": 1,
                            "evaluation_status": "passed", "evaluation_notes": None}
                except Exception as exc:
                    logger.warning("skill generation failed for %s: %s", qd["question_number"], exc)
                    return None

        skills: list[dict] = [s for s in await asyncio.gather(*[_gen_one(qd) for qd in qdata]) if s]
        skipped = [qd["question_number"] for qd in qdata if qd["question_number"] not in {s["q"]["question_number"] for s in skills}]
        # Only a TOTAL wipeout fails the job — partial success still locks the skills that built.
        if not skills:
            raise RuntimeError("checking-skill generation produced no usable skills for any question")
        if skipped:
            logger.warning("skill generation skipped %d/%d question(s): %s", len(skipped), total, skipped)

        # Evaluate the generated skills (lenient gate), then improve only the weak ones.
        await _job(progress=74, step="Evaluating checking skills")
        verdicts = await _evaluate_skills(skills, custom_instruction=custom_instruction, test_id=test_uuid, svc=svc)
        weak = [s for s in skills if verdicts.get(s["q"]["question_number"], {}).get("status") == "failed"]
        if weak:
            await _job(progress=84, step=f"Improving {len(weak)} weak skill(s)")

            async def _improve_one(s) -> None:
                qd = s["q"]
                fb = verdicts.get(qd["question_number"], {})
                feedback = " ".join(filter(None, [
                    "; ".join(fb.get("issues") or []), fb.get("fix_feedback") or "",
                ])) or "Make the guide more usable and correctly mapped."
                async with gen_sem:
                    try:
                        async with AsyncSessionLocal() as idb:
                            s["skill_json"] = await SkillGeneratorAgent(idb).improve(
                                question_number=qd["question_number"], question_text=qd["question_text"], marks=qd["marks"],
                                topic=qd["topic"], subtopic=qd["subtopic"], model_answer=model_answer, rubric=rubric_text,
                                custom_instruction=custom_instruction, knowledge=s["knowledge"],
                                previous_skill=s["skill_json"], evaluator_feedback=feedback, test_id=test_uuid,
                            )
                        s["iterations"] = 2
                    except Exception as exc:
                        logger.warning("skill improve failed for %s: %s", qd["question_number"], exc)

            await asyncio.gather(*[_improve_one(s) for s in weak])
            verdicts2 = await _evaluate_skills(weak, custom_instruction=custom_instruction, test_id=test_uuid, svc=svc)
            verdicts.update(verdicts2)

        # SAVE: lock the skills (no admin gate). Residual failure → passed_with_warning.
        await _job(progress=92, step="Locking checking skills")
        async with AsyncSessionLocal() as db:
            for s in skills:
                qd = s["q"]
                v = verdicts.get(qd["question_number"], {})
                status = v.get("status") or "passed"
                if status == "failed":
                    status = "passed_with_warning"
                notes = " ".join(filter(None, [
                    "; ".join(v.get("issues") or []), v.get("fix_feedback") or "",
                ])) or None
                db.add(QuestionSpecificCheckingSkill(
                    test_id=test_uuid, question_id=qd["id"], skill_json=s["skill_json"], version=1, is_active=True,
                    evaluation_status=status, evaluation_notes=notes, iterations=s["iterations"],
                ))
            t = (await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == test_uuid))).scalar_one()
            t.skill_generation_status = "completed"
            await db.commit()

        await _job(status=JobStatus.completed, progress=100, step="Test ready",
                   output={"questions": total, "skills_locked": len(skills), "skipped_questions": skipped,
                           "total_marks": total_marks, "improved": len(weak)})

    try:
        run_task(work, job_id=job_id, task=self, manage_session=False)
    except MarksReconciliationError as exc:
        # Terminal + non-transient: run_task already recorded the job as failed with this
        # message. Just flip the test's status and STOP — retrying would re-read the same
        # paper to the same mismatch. The admin fixes the marks / paper and regenerates.
        logger.error("generate_test_skills marks reconciliation failed: %s", exc)
        _mark_skill_failed(test_id)
    except Exception as exc:
        logger.exception("generate_test_skills failed: %s", exc)
        _mark_skill_failed(test_id)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


async def _evaluate_skills(skills: list[dict], *, custom_instruction, test_id, svc) -> dict[str, dict]:
    """Run the SkillEvaluator over `skills` and return {question_number: verdict}.
    Opens its own short-lived session (borrow-per-use). `s["q"]` are plain question
    dicts. Best-effort: on evaluator failure, treat all as passed (never block)."""
    from app.core.database import AsyncSessionLocal
    from app.ai.agents.skill_evaluator_agent import SkillEvaluatorAgent
    payload = [{
        "question_number": s["q"]["question_number"], "marks": s["q"]["marks"],
        "question_text": s["q"]["question_text"], "topic": s["q"]["topic"], "subtopic": s["q"]["subtopic"],
        "skill_json": s["skill_json"],
    } for s in skills]
    valid = [s["q"]["question_number"] for s in skills]
    try:
        async with AsyncSessionLocal() as db:
            result = await SkillEvaluatorAgent(db).evaluate(
                skills=payload, custom_instruction=custom_instruction, test_id=test_id,
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
    # Borrow-per-use session model (run_task manage_session=False): this pipeline runs
    # for many minutes (vision extraction, checker/reviewer, ~40 locator calls). A single
    # session held across it would sit idle through each AI phase and be dropped
    # server-side, crashing the next commit. Instead `work()` takes NO long-lived session
    # — every DB touch opens a short-lived session and returns the connection to the pool,
    # so nothing is ever held idle across an AI/render phase (LOAD → WORK → SAVE).
    async def work() -> None:
        import asyncio
        from sqlalchemy import delete, select
        from app.core.database import AsyncSessionLocal
        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from app.modules.jobs.models import JobStatus
        from app.modules.jobs.service import update_job
        from app.modules.subjective import service as svc
        from app.modules.subjective.models import (
            AnswerEvaluation, AnswerExtraction, AnswerQualityCheck, PDFAnnotation,
            QuestionSpecificCheckingSkill, StudentAnswerSheet, SubjectiveQuestion, SubjectiveTest,
        )
        from app.ai.agents.answer_structure_agent import AnswerStructureAgent
        from app.ai.agents.answer_extraction_agent import AnswerExtractionAgent
        from app.ai.agents.answer_evaluation_agent import AnswerEvaluationAgent
        from app.ai.agents.answer_reviewer_agent import AnswerReviewerAgent
        from app.core.config import settings
        from app.processing import pdf_tools, image_quality, annotation as annotate
        from app.processing import annotation_geometry as geom

        jid = uuid.UUID(job_id)
        sid = uuid.UUID(sheet_id)

        # ── tiny borrow-per-use helpers (each opens + closes its own session) ────
        async def _job(**kw) -> None:
            async with AsyncSessionLocal() as db:
                await update_job(db, jid, **kw)

        async def _set_sheet_status(status: str) -> None:
            async with AsyncSessionLocal() as db:
                s = (await db.execute(select(StudentAnswerSheet).where(StudentAnswerSheet.id == sid))).scalar_one_or_none()
                if s:
                    s.current_status = status
                    await db.commit()

        # ── LOAD: snapshot everything WORK needs as plain data, then release the
        # connection. Scalar columns on the detached ORM objects stay readable
        # (expire_on_commit=False), so `questions` can be reused read-only later. ─
        async with AsyncSessionLocal() as db:
            sheet = (await db.execute(select(StudentAnswerSheet).where(StudentAnswerSheet.id == sid))).scalar_one_or_none()
            if not sheet:
                raise ValueError(f"StudentAnswerSheet {sheet_id} not found")
            test = (await db.execute(select(SubjectiveTest).where(SubjectiveTest.id == sheet.test_id))).scalar_one()
            questions = await svc.get_test_questions(db, test.id)
            file_record = (await db.execute(select(File).where(File.id == sheet.file_id))).scalar_one_or_none()
            if not file_record:
                raise ValueError("Answer-sheet file not found")
            student_id = sheet.student_id
            upload_attempt = sheet.upload_attempt_number
            test_id = test.id
            exam_id = test.exam_id
            display_name = test.display_name
            custom_instruction = test.custom_instruction
            rubric_file_id = test.rubric_file_id
            r2_key = file_record.r2_key
            mime_type = file_record.mime_type
            valid_numbers = [q.question_number for q in questions]
            full_marks_by_qid = {q.question_number: q.marks for q in questions}
            await update_job(db, jid, status=JobStatus.processing, progress=8, step="Rendering pages")

        # ── Idempotency for retries ─────────────────────────────────────────────
        # A Celery retry (max_retries=1) re-runs work() from the top, and every phase
        # below is insert-only (quality check, extraction, evaluation, annotation +
        # checked-PDF File). Clear any rows a prior attempt created for this sheet so a
        # retry REPLACES rather than duplicates them. The checked-PDF R2 key is
        # deterministic ("answer-sheets/checked/{sid}/checked.pdf") and overwritten in
        # place, so we also drop the stale checked-File row that pointed at it.
        checked_key_for_sheet = f"answer-sheets/checked/{sid}/checked.pdf"
        async with AsyncSessionLocal() as db:
            for model in (AnswerQualityCheck, AnswerExtraction, AnswerEvaluation, PDFAnnotation):
                await db.execute(delete(model).where(model.sheet_id == sid))
            await db.execute(delete(File).where(File.r2_key == checked_key_for_sheet))
            await db.commit()

        # ── Render pages (no DB held) ───────────────────────────────────────────
        file_bytes = await asyncio.to_thread(get_r2().download_fileobj, r2_key)
        pages = pdf_tools.render_to_page_images(file_bytes, mime_type)
        page_map = {p.page_number: p for p in pages}
        page_pngs = [p.png_bytes for p in pages]

        # 1) Quality gate ────────────────────────────────────────────────────────
        await _job(progress=15, step="Checking image quality")
        metrics = image_quality.assess(page_pngs)
        async with AsyncSessionLocal() as db:
            db.add(AnswerQualityCheck(
                sheet_id=sid, blur_score=metrics.blur_score, brightness_score=metrics.brightness_score,
                tilt_angle=metrics.tilt_angle, resolution_ok=metrics.resolution_ok,
                readability_score=metrics.readability_score, overall_status=metrics.overall_status,
                quality_notes=metrics.quality_notes,
            ))
            await db.commit()
        if metrics.overall_status == "poor" and upload_attempt < svc.MAX_UPLOAD_ATTEMPTS:
            await _set_sheet_status("needs_reupload")
            await _job(status=JobStatus.completed, progress=100, step="Re-upload requested",
                       output={"needs_reupload": True, "quality_notes": metrics.quality_notes})
            return

        # 2a) Whole-sheet STRUCTURE pass — one Gemini call on its own session ─────
        await _job(progress=20, step="Detecting sheet structure")
        async with AsyncSessionLocal() as db:
            structure_map = await AnswerStructureAgent(db).detect(
                page_pngs=page_pngs, valid_numbers=valid_numbers, sheet_id=sid,
            )

        # 2b) Question-level extraction — all pages CONCURRENTLY, each on its OWN
        # short-lived session (audit logging writes to the DB; one AsyncSession is
        # not concurrency-safe). Partial success: a failing page → empty output. ──
        await _set_sheet_status("extracting")
        await _job(progress=24, step=f"Extracting {len(pages)} pages in parallel")
        extract_sem = asyncio.Semaphore(6)

        async def _extract_one(page) -> dict:
            nxt = AnswerStructureAgent.page_hint(structure_map, page.page_number + 1)
            async with extract_sem:
                try:
                    async with AsyncSessionLocal() as pdb:
                        return await AnswerExtractionAgent(pdb).extract_page(
                            page_png=page.png_bytes, page_number=page.page_number,
                            width=page.width, height=page.height,
                            valid_numbers=valid_numbers, sheet_id=sid,
                            structure_hint=AnswerStructureAgent.page_hint(structure_map, page.page_number),
                            prev_page_tail="",
                            next_page_hint=(f"Next page is expected to hold: {nxt}" if nxt else ""),
                        )
                except Exception as exc:
                    logger.warning("page %s extraction failed (continuing): %s", page.page_number, exc)
                    return {"page": page.page_number, "page_size": [page.width, page.height],
                            "answers": [], "page_confidence": 0.0}

        page_outputs = list(await asyncio.gather(*[_extract_one(p) for p in pages]))
        confidences: list[float] = [po.get("page_confidence", 0.0) for po in page_outputs]

        extraction = svc.assemble_questionwise(page_outputs, questions)
        extraction["structure_map"] = structure_map
        overall_conf = round(sum(confidences) / len(confidences), 3) if confidences else None
        async with AsyncSessionLocal() as db:
            db.add(AnswerExtraction(
                sheet_id=sid, extracted_data=extraction,
                overall_confidence=overall_conf, model_used=settings.MODEL_VISION,
            ))
            await db.commit()

        # If vision read NO answer text from any page (upside-down / blank / unreadable
        # scan), checking would silently produce a 0-mark "completed" sheet. Treat that
        # like the quality gate: ask for a clearer re-upload (no AI marking spent), or —
        # if attempts are exhausted — fail honestly instead of recording an unfair 0.
        extracted_chars = sum(len((q.get("answer_text") or "").strip())
                              for q in extraction.get("questions", []))
        if extracted_chars == 0:
            note = "Could not read any answers from the uploaded sheet — please re-upload a clearer scan."
            if upload_attempt < svc.MAX_UPLOAD_ATTEMPTS:
                await _set_sheet_status("needs_reupload")
                await _job(status=JobStatus.completed, progress=100, step="Re-upload requested",
                           output={"needs_reupload": True, "quality_notes": note})
                return
            # Attempts exhausted: fail honestly rather than record an unfair 0. Raising
            # routes through the task's failure path (_mark_sheet_failed → sheet `failed`,
            # run_task → terminal failed); a normal return here would be marked completed.
            raise ValueError(note)

        # Locked per-question checking skills + optional rubric text (one session) ─
        async with AsyncSessionLocal() as db:
            sk_r = await db.execute(
                select(QuestionSpecificCheckingSkill, SubjectiveQuestion.question_number)
                .join(SubjectiveQuestion, SubjectiveQuestion.id == QuestionSpecificCheckingSkill.question_id)
                .where(QuestionSpecificCheckingSkill.test_id == test_id)
            )
            skills_by_qid = {qnum: skill.skill_json for skill, qnum in sk_r.all()}
            rubric_text = await _resolve_text(db, rubric_file_id) or None

        # 3) Checker — one gpt-5.5 call on its own session ───────────────────────
        await _set_sheet_status("evaluating")
        await _job(progress=54, step="Checking answers")
        checker_questions = [{
            "qid": q["qid"], "question_text": q["question_text"], "marks": q["marks"],
            "answer_text": q["answer_text"], "page_numbers": q["page_numbers"],
        } for q in extraction["questions"]]
        async with AsyncSessionLocal() as db:
            initial_eval = await AnswerEvaluationAgent(db).evaluate(
                questions=checker_questions, skills_by_qid=skills_by_qid,
                rubric_text=rubric_text, custom_instruction=custom_instruction, sheet_id=sid,
            )
        clamped_initial, _, _ = svc.clamp_marks(copy.deepcopy(initial_eval), questions)

        # 4) Reviewer / verification pass — one gpt-5.5 call on its own session ───
        await _set_sheet_status("reviewing")
        await _job(progress=66, step="Reviewer verification pass")
        async with AsyncSessionLocal() as db:
            reviewed = await AnswerReviewerAgent(db).review(
                evaluation=clamped_initial, full_marks_by_qid=full_marks_by_qid, sheet_id=sid,
            )
        review_notes = reviewed.get("review_notes")
        final_eval, awarded, possible = svc.clamp_marks(reviewed, questions)

        # SAVE evaluation + flip to feedback_ready (one atomic burst) ────────────
        # PROGRESSIVE FEEDBACK SPLIT (spec §6.5): marks + feedback are final, expose
        # them immediately so the student + feedback chatbot unlock while the checked
        # PDF is still being annotated below. Annotation is best-effort.
        async with AsyncSessionLocal() as db:
            db.add(AnswerEvaluation(
                sheet_id=sid, evaluation_data=final_eval, initial_evaluation_data=initial_eval,
                reviewed=True, review_notes=review_notes, total_marks_awarded=awarded,
                total_marks_possible=possible, overall_confidence=overall_conf, model_used="gpt-5.5",
            ))
            s = (await db.execute(select(StudentAnswerSheet).where(StudentAnswerSheet.id == sid))).scalar_one_or_none()
            if s:
                s.current_status = "feedback_ready"
            await db.commit()
        await _job(progress=70, step="Feedback ready — annotating PDF")

        # ── Personalization (best-effort, own session) ──────────────────────────
        try:
            async with AsyncSessionLocal() as db:
                await _log_subjective_activity(
                    db, student_id=student_id, sheet_id=sid, exam_id=exam_id,
                    display_name=display_name, final_eval=final_eval, awarded=awarded, possible=possible,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("subjective personalization hook failed (continuing): %s", exc)

        # 5) Locate + validate annotation geometry → checked PDF (best-effort) ────
        # Marks are ALREADY final and the sheet is `feedback_ready`, so from here on the
        # result must NEVER be lost. The annotation is wrapped in its own time budget
        # (ANNOTATION_BUDGET_SECONDS): if the ~40-call locator loop runs long on a big
        # sheet, the inner timeout settles the sheet to `checked` (feedback stands, PDF
        # simply unavailable) rather than letting the outer task timeout fire here and
        # flip a fully-graded sheet to `failed`. Each locator call opens its own
        # short-lived session, so the loop holds no idle connection.
        async def _annotate() -> None:
            await _job(progress=78, step="Locating annotations")
            regions_by_q = _regions_by_question(extraction)
            commands_by_page, locator_plans = await _locate_and_build_commands(
                final_eval, regions_by_q, page_map, geom, sid,
            )

            await _job(progress=90, step="Drawing checked PDF")
            _add_marks_and_banner(commands_by_page, final_eval, regions_by_q, page_map, awarded, possible)

            annotated_pngs = [
                annotate.draw_annotations(page.png_bytes, commands_by_page.get(page.page_number, []))
                for page in pages
            ]
            checked_pdf = pdf_tools.build_pdf_from_images(annotated_pngs)

            checked_key = f"answer-sheets/checked/{sid}/checked.pdf"
            await asyncio.to_thread(
                get_r2().upload_fileobj, checked_key, io.BytesIO(checked_pdf), "application/pdf"
            )
            async with AsyncSessionLocal() as db:
                checked_file = File(
                    original_filename="checked.pdf", display_name=f"{display_name} — Checked",
                    mime_type="application/pdf", file_size=len(checked_pdf), r2_key=checked_key,
                    uploaded_by=student_id,
                )
                db.add(checked_file)
                await db.flush()
                db.add(PDFAnnotation(
                    sheet_id=sid,
                    annotation_instructions={"pages": {str(k): v for k, v in commands_by_page.items()}},
                    locator_plan={"targets": locator_plans},
                    checked_file_id=checked_file.id, annotation_status="completed",
                ))
                s = (await db.execute(select(StudentAnswerSheet).where(StudentAnswerSheet.id == sid))).scalar_one_or_none()
                if s:
                    s.current_status = "checked"
                await db.commit()

        annotation_ok = False
        try:
            await asyncio.wait_for(_annotate(), timeout=ANNOTATION_BUDGET_SECONDS)
            annotation_ok = True
        except Exception as exc:  # includes asyncio.TimeoutError from the inner budget
            # Annotation is secondary — the feedback stands. Record the failure on a
            # fresh session and still settle the sheet to `checked` (the result page
            # shows feedback; the checked PDF is simply unavailable).
            logger.warning("annotation phase failed (feedback already saved): %s", exc)
            async with AsyncSessionLocal() as db:
                db.add(PDFAnnotation(sheet_id=sid, annotation_status="failed"))
                s = (await db.execute(select(StudentAnswerSheet).where(StudentAnswerSheet.id == sid))).scalar_one_or_none()
                if s:
                    s.current_status = "checked"
                await db.commit()
            annotation_ok = False

        await _job(status=JobStatus.completed, progress=100, step="Checked",
                   output={"total_marks_awarded": awarded, "total_marks_possible": possible,
                           "quality_status": metrics.overall_status, "annotated": annotation_ok})

    try:
        run_task(work, job_id=job_id, task=self, manage_session=False)
    except Exception as exc:
        logger.exception("check_answer_sheet failed: %s", exc)
        _mark_sheet_failed(sheet_id)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


# ── personalization hook ───────────────────────────────────────────────────────

async def _log_subjective_activity(db, *, student_id, sheet_id, exam_id, display_name,
                                   final_eval, awarded, possible) -> None:
    """Record the subjective test as a personalization activity, then enqueue the
    extended-subjective + daily roll-ups (best-effort, off the checking critical path).
    Takes plain values (not ORM objects) so the caller can run it on a fresh short-lived
    session after the source rows have been detached."""
    from app.modules.personalization import service as pers
    qrs = (final_eval or {}).get("question_results", []) if isinstance(final_eval, dict) else []
    lines = []
    for q in qrs:
        miss = ", ".join((q.get("missing_points") or [])[:3])
        lines.append(
            f"Q{q.get('question_number')}: {q.get('awarded_marks')}/{q.get('max_marks')}"
            + (f" — feedback: {q.get('feedback')}" if q.get("feedback") else "")
            + (f" — missing: {miss}" if miss else "")
        )
    test_text = (f"Subjective test '{display_name}': {awarded}/{possible} total.\n" + "\n".join(lines))[:6000]
    summary = f"Subjective test '{display_name}': {awarded}/{possible} marks."
    newly_logged = await pers.log_activity(
        db, student_id=student_id, activity_type="subjective_test", entity_id=sheet_id,
        exam_id=exam_id, raw_context={"summary": test_text}, summary_line=summary,
    )
    # Only roll up when this is a genuinely new activity — on a checking-task retry the
    # activity is already logged, so we must not re-run the (non-idempotent) extended-
    # subjective summary that would double-count this sheet's mistakes.
    if not newly_logged:
        return
    try:
        from app.core.celery_client import get_celery
        get_celery().send_task(
            "workers.tasks.personalization_tasks.pers_update_subjective",
            args=[str(student_id), test_text], queue="kvi_ai_default",
        )
    except Exception:  # noqa: BLE001
        pass


# ── extraction helpers ───────────────────────────────────────────────────────────

def _prev_page_tail(page_outputs: list[dict]) -> str:
    """A short carry-forward hint from the previous page: its last question and whether
    that answer was flagged as continuing, so the extractor can attribute unlabeled
    writing at the top of this page."""
    if not page_outputs:
        return ""
    last = page_outputs[-1]
    answers = last.get("answers") or []
    if not answers:
        return ""
    a = answers[-1]
    qn = a.get("question_number")
    tail = (a.get("answer_text") or "").strip().replace("\n", " ")
    tail = tail[-160:]
    cont = " (this answer was marked as continuing)" if a.get("continues") else ""
    if not qn and not tail:
        return ""
    return (f"Previous page ended with question {qn}{cont}. Its last words were: "
            f"\"...{tail}\". If this page starts with unlabeled writing, it likely continues question {qn}.")


# ── annotation helpers ───────────────────────────────────────────────────────────

def _regions_by_question(extraction: dict) -> dict[str, list[dict]]:
    """{question_number: [{page, question_bbox}]} from the stored extraction."""
    out: dict[str, list[dict]] = {}
    for q in extraction.get("questions", []):
        out[str(q.get("qid"))] = q.get("page_regions") or []
    return out


def _positive_sections(qres: dict) -> list[dict]:
    """Fully-correct sections with evidence text → become a teacher's tick beside the
    good point. (Partial sections get no inline tick; the section-wise breakdown lives in
    the result UI. No section fractions are drawn on the PDF.)"""
    out: list[dict] = []
    for s in qres.get("sections") or []:
        if not isinstance(s, dict):
            continue
        if s.get("status") == "correct" and (s.get("evidence_text") or "").strip():
            out.append({
                "section": s.get("section"), "evidence_text": s.get("evidence_text"),
            })
    return out[:MAX_TARGETS_PER_QUESTION]


def _norm_text(s: str) -> str:
    """Lowercased alphanumeric-only form (keeps Devanagari letters) for fuzzy matching."""
    return "".join(ch.lower() for ch in (s or "") if ch.isalnum())


def _ngrams(s: str, n: int = 4) -> set[str]:
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else ({s} if s else set())


def _section_page(evidence_text: str, regions: list[dict]) -> int | None:
    """Pick the page whose extracted answer text contains / best matches this section's
    evidence, so its tick is located on the RIGHT page of a multi-page answer. Returns
    None when no page matches confidently (caller then skips the tick)."""
    ev = _norm_text(evidence_text)
    if not ev:
        return None
    probe = ev[:80]
    ev_grams = _ngrams(ev)
    best_page, best_score = None, 0.0
    for r in regions:
        pt = _norm_text(r.get("answer_text"))
        if not pt:
            continue
        if probe and probe in pt:
            return r.get("page")
        if ev_grams:
            inter = len(ev_grams & _ngrams(pt))
            score = inter / len(ev_grams)
            if score > best_score:
                best_page, best_score = r.get("page"), score
    return best_page if best_score >= 0.30 else None


async def _locate_and_build_commands(final_eval, regions_by_q, page_map, geom, sheet_id):
    """One vision locator call per (question, page): finds underline paths for wrong
    items AND placement for positive ticks/section marks, validates geometry, and emits
    draw commands. Returns (commands_by_page, locator_plans). Section ticks/marks always
    appear (region fallback in the validator); underlines use the safety ladder. Stays
    uncrowded via per-page/per-question caps.

    The per-(question,page) locator calls are independent, so they run CONCURRENTLY
    (asyncio.gather + Semaphore), each opening its OWN short-lived DB session for the
    agent's audit log — no pooled connection is held idle across the vision calls.
    A deterministic PLAN pass (in question order) selects which calls to make and
    enforces the per-page cap up front, so the parallel execution can't change which
    items get marked or the on-page command order."""
    import asyncio
    from app.ai.agents.annotation_locator_agent import crop_region, AnnotationLocatorAgent
    from app.core.database import AsyncSessionLocal

    # ── PLAN (deterministic, no AI): pick the (question, page) calls in question order,
    # applying MAX_TARGETS_PER_PAGE up front so selection doesn't depend on timing. ──
    jobs: list[dict] = []
    page_plan_count: dict[int, int] = {}
    for qres in final_eval.get("question_results", []):
        qnum = str(qres.get("question_number") or "")
        regions = [r for r in regions_by_q.get(qnum, []) if r.get("page") in page_map]
        if not regions:
            continue
        primary = max(regions, key=lambda r: _bbox_area(r.get("question_bbox")))
        primary_page = primary.get("page")

        # Group wrong targets by the page they sit on (default = primary page).
        targets = [t for t in (qres.get("annotation_targets") or [])
                   if isinstance(t, dict) and (t.get("target_text") or "").strip()]
        targets = targets[:MAX_TARGETS_PER_QUESTION]
        targets_by_page: dict[int, list[dict]] = {}
        for t in targets:
            pno = t.get("page_number") if t.get("page_number") in page_map else primary_page
            targets_by_page.setdefault(pno, []).append(t)

        # Route each positive section's tick to the page its evidence actually sits on
        # (a multi-page answer has good points spread across pages). Skip if unmatched.
        sections_by_page: dict[int, list[dict]] = {}
        for s in _positive_sections(qres):
            spage = _section_page(s.get("evidence_text"), regions)
            if spage in page_map:
                sections_by_page.setdefault(spage, []).append(s)

        # Sorted for determinism (set iteration order is otherwise arbitrary).
        for page_no in sorted(set(targets_by_page) | set(sections_by_page)):
            page = page_map.get(page_no)
            if not page or page_plan_count.get(page_no, 0) >= MAX_TARGETS_PER_PAGE:
                continue
            page_targets = targets_by_page.get(page_no, [])
            page_sections = sections_by_page.get(page_no, [])
            if not page_targets and not page_sections:
                continue
            qbbox = next((r.get("question_bbox") for r in regions if r.get("page") == page_no), None)
            jobs.append({"qnum": qnum, "page_no": page_no, "page": page, "qbbox": qbbox,
                         "page_targets": page_targets, "page_sections": page_sections})
            page_plan_count[page_no] = page_plan_count.get(page_no, 0) + 1

    # ── LOCATE (parallel): one vision call per planned job, capped concurrency. ──────
    sem = asyncio.Semaphore(LOCATOR_CONCURRENCY)

    async def _locate(job: dict) -> dict:
        page, page_no, qnum, qbbox = job["page"], job["page_no"], job["qnum"], job["qbbox"]
        crop_png, crop_origin, cw, ch = crop_region(page.png_bytes, qbbox, page.width, page.height)
        async with sem:
            try:
                async with AsyncSessionLocal() as ldb:
                    return await AnnotationLocatorAgent(ldb).locate_question(
                        crop_png=crop_png, crop_origin=crop_origin, crop_w=cw, crop_h=ch,
                        page_number=page_no, question_number=qnum,
                        targets=job["page_targets"], sections=job["page_sections"], sheet_id=sheet_id,
                    )
            except Exception as exc:
                logger.warning("locator failed for Q%s p%s (continuing): %s", qnum, page_no, exc)
                return {"page_number": page_no, "question_number": qnum, "targets": [], "section_marks": []}

    locations = await asyncio.gather(*[_locate(j) for j in jobs])

    # ── BUILD (deterministic, in planned order): validate geometry + emit commands. ──
    commands_by_page: dict[int, list[dict]] = {}
    locator_plans: list[dict] = []

    def add(page_no: int, cmd: dict) -> None:
        commands_by_page.setdefault(page_no, []).append(cmd)

    for job, loc in zip(jobs, locations):
        page, page_no, qbbox = job["page"], job["page_no"], job["qbbox"]
        plan = geom.validate_question_plan(loc, (page.width, page.height), qbbox)
        locator_plans.append(plan)

        for tp in plan.get("targets", []):
            if tp.get("final_underline_paths"):
                add(page_no, {"type": "underline_path", "paths": tp["final_underline_paths"]})
            if tp.get("final_comment_box") and (tp.get("comment_text") or "").strip():
                add(page_no, {"type": "comment", "text": tp["comment_text"], "box": tp["final_comment_box"]})
        # Positive sections become a tick beside the located good point (no section
        # fraction on the PDF — the breakdown lives in the result UI).
        for sm in plan.get("section_marks", []):
            tpoint = sm.get("tick_point")
            if isinstance(tpoint, (list, tuple)) and len(tpoint) >= 2:
                add(page_no, {"type": "tick", "x": int(tpoint[0]), "y": int(tpoint[1])})
    return commands_by_page, locator_plans


def _bbox_area(bbox) -> float:
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            return float(bbox[2]) * float(bbox[3])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _add_marks_and_banner(commands_by_page, final_eval, regions_by_q, page_map, awarded, possible) -> None:
    first_page = min(page_map.keys()) if page_map else 1

    def add(page_no: int, cmd: dict) -> None:
        commands_by_page.setdefault(page_no, []).append(cmd)

    for qres in final_eval.get("question_results", []):
        qnum = str(qres.get("question_number") or "")
        regions = [r for r in regions_by_q.get(qnum, []) if r.get("question_bbox")]
        if not regions:
            continue
        # Place the mark at the END of the answer: the LAST page the question occupies,
        # and the bottom-most region on it — like a teacher's total after the last line.
        last_page = max(r.get("page") for r in regions)
        page_regions = [r for r in regions if r.get("page") == last_page]
        region = max(page_regions, key=lambda r: (r["question_bbox"][1] + r["question_bbox"][3]))
        page = page_map.get(region.get("page"))
        if not page:
            continue
        x, y, w, h = region["question_bbox"]
        mark_text = f"{_fmt(qres.get('awarded_marks', 0))}/{_fmt(qres.get('max_marks', 0))}"
        mx = min(int(page.width * 0.88), int(x + w - 10))
        my = int(y + h - 6)
        if region["page"] == first_page and my < 90:   # don't collide with the total banner
            my = 96
        # Don't let the question total overlap a section mark/tick already on this page.
        my = _avoid_collision(mx, my, commands_by_page.get(region["page"], []), page.height)
        add(region["page"], {"type": "mark", "text": mark_text, "x": mx, "y": my})
    add(first_page, {"type": "banner", "text": f"Total: {_fmt(awarded)} / {_fmt(possible)}"})


def _avoid_collision(x: int, y: int, existing: list[dict], page_h: int,
                     gap: int = 150, x_thresh: int = 280) -> int:
    """Nudge y so a circled mark at (x, y) doesn't overlap an already-placed section
    mark/tick at a similar x. Pushes up (marks sit at the answer end) then down."""
    def clashes(yy: int) -> bool:
        for c in existing:
            if c.get("type") not in ("tick", "mark"):
                continue
            cx, cy = c.get("x"), c.get("y")
            if cx is None or cy is None:
                continue
            if abs(int(cx) - x) < x_thresh and abs(int(cy) - yy) < gap:
                return True
        return False

    if not clashes(y):
        return y
    for step in range(1, 12):
        up = y - step * gap
        if up > gap and not clashes(up):
            return up
        down = y + step * gap
        if down < page_h - gap and not clashes(down):
            return down
    return y


def _fmt(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f == int(f) else str(round(f, 1))


# ── text-resolution + failure helpers ────────────────────────────────────────────

async def _resolve_paper_source(db, file_id) -> tuple[str, object]:
    """Decide how the question paper should be READ and return the source for it:
      • ("text", paper_text)   — the paper has a usable embedded text layer (≥30 chars).
      • ("images", [png, ...]) — scanned / no-text-layer PDF or image → page PNGs @300 DPI
        for DIRECT vision extraction (one faithful pass, marks read from the printed digit),
        instead of the old lossy OCR→text→extract chain.
    Mirrors `_resolve_text`'s text-vs-vision decision. (Preeti/scanned .docx has no image
    render path here, same as before — upload such papers as PDF.)"""
    if not file_id:
        return "text", ""
    from sqlalchemy import select
    from app.integrations.r2_client import get_r2
    from app.modules.files.models import File
    from app.processing.document_text import extract_text_from_bytes
    from app.processing import pdf_tools

    f = (await db.execute(select(File).where(File.id == file_id))).scalar_one_or_none()
    if not f:
        return "text", ""
    data = await asyncio.to_thread(get_r2().download_fileobj, f.r2_key)

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
        return "text", text
    try:
        pages = pdf_tools.render_to_page_images(data, f.mime_type, dpi=300)
    except Exception:
        return "images", []
    return "images", [p.png_bytes for p in pages[:15]]


async def _resolve_text(db, file_id, handwritten: bool = False) -> str:
    """Best-effort text for a stored file: direct text extraction, with a vision OCR
    fallback for scanned PDFs / images that carry no embedded text. `handwritten` routes
    that vision fallback to Gemini (handwriting) vs Azure gpt-5 typed vision (CLAUDE.md §4)."""
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
    data = await asyncio.to_thread(get_r2().download_fileobj, f.r2_key)

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
    return await _vision_ocr(db, data, f.mime_type, file_id, handwritten=handwritten)


async def _vision_ocr(db, data: bytes, mime_type: str, entity_id, handwritten: bool = False) -> str:
    """Vision OCR fallback for scanned / no-text-layer papers, model answers and rubrics.

    Uses the hardened "you are an OCR engine — transcribe EXACTLY, never interpret /
    summarise / refuse" transcription prompt shared with the Knowledge layer
    (`VISION_EXTRACT_PROMPT`), at 300 DPI, with per-page retries. The previous one-line
    prompt + JSON wrapper let gpt-5 (a reasoning model) INTERMITTENTLY REFUSE — it would
    return a short "image resolution is insufficient, please resend a clearer scan"
    message instead of the text. That refusal passed the non-empty guard in `_resolve_text`
    and fed the extractor no questions, so skill generation failed with "no questions could
    be extracted" on some runs and succeeded on others (pure model luck). The strict
    OCR-engine prompt transcribes this same page reliably. Typed text → Azure gpt-5 typed
    vision; handwritten Nepali/Devanagari → Gemini (CLAUDE.md §4 governing principle)."""
    from app.ai.model_router import get_provider
    from app.ai.agents.knowledge_processing_agent import VISION_EXTRACT_PROMPT
    from app.processing import pdf_tools

    try:
        # 300 DPI matches the Knowledge OCR path — enough pixels/glyph for dense Devanagari.
        pages = pdf_tools.render_to_page_images(data, mime_type, dpi=300)
    except Exception:
        return ""
    provider = get_provider("vision" if handwritten else "vision_typed")
    OCR_ATTEMPTS = 3
    parts: list[str] = []
    for page in pages[:15]:
        page_text = ""
        for attempt in range(1, OCR_ATTEMPTS + 1):
            try:
                # schema=None → plain-text transcription (the strict prompt says "output
                # only the transcribed text"); both Azure and Gemini return {"text": ...}.
                res = await provider.generate_with_image(
                    VISION_EXTRACT_PROMPT, page.png_bytes, schema=None,
                    audit_ctx={"db": db, "agent_type": "QuestionPaperAgent", "task_type": "paper_vision_ocr",
                               "entity_type": "file", "entity_id": entity_id},
                )
                text = str(res.get("text") or "").strip() if isinstance(res, dict) else ""
                if text:
                    page_text = text
                    break
            except Exception as exc:
                logger.warning("vision OCR failed on a page (attempt %d/%d): %s",
                               attempt, OCR_ATTEMPTS, exc)
        if page_text:
            parts.append(page_text)
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
                if not s:
                    return
                # NEVER downgrade a sheet whose marks are already final. Once the
                # reviewer pass commits `feedback_ready` (and certainly once `checked`),
                # the result is student-visible and must survive any later failure —
                # e.g. an outer task timeout/cancel during the best-effort annotation
                # phase. Settle such a sheet to `checked` (PDF may be absent) instead.
                if s.current_status in ("feedback_ready", "checked"):
                    s.current_status = "checked"
                    await db.commit()
                    logger.warning("Sheet %s already graded; settled to checked instead of failed", sheet_id)
                    return
                s.current_status = "failed"
                await db.commit()
        except Exception:
            logger.exception("Could not mark sheet %s failed", sheet_id)

    try:
        run_async(_do())
    except Exception:
        logger.exception("mark_sheet_failed wrapper failed")
