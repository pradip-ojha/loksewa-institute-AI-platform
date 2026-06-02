import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.modules.jobs.service import update_job
from app.modules.mcq.models import MCQDocument, MCQReviewBatch, MCQQuestion

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are an expert MCQ extraction system for competitive exam preparation.

Extract all multiple-choice questions from the provided document content.

CRITICAL RULES:
1. Each question must have EXACTLY 4 options normalized to IDs: A, B, C, D
2. Detect the correct-answer format automatically (A/B/C/D, Nepali letters क/ख/ग/घ, numbers 1/2/3/4, Nepali numbers १/२/३/४) and normalize correct_option_ids to ["A"], ["B"], ["C"], or ["D"]
3. Preserve question text and option text exactly as written (Nepali Devanagari must be preserved)
4. Extract explanation exactly as written in the document
5. If explanation is missing for a question, set explanation to null and set needs_explanation_review to true
6. Assign complexity: definition recall = "easy", application = "medium", multi-step analysis = "hard"
7. Assign topic and subtopic if determinable from context

Active skill instructions:
{skill_instructions}

Topic hint: {topic_hint}
Subtopic hint: {subtopic_hint}
Custom instruction: {custom_instruction}

Document content:
{document_text}

Return ONLY valid JSON matching this exact structure:
{{
  "questions": [
    {{
      "question_text": "...",
      "options": [
        {{"id": "A", "label": "A", "text": "..."}},
        {{"id": "B", "label": "B", "text": "..."}},
        {{"id": "C", "label": "C", "text": "..."}},
        {{"id": "D", "label": "D", "text": "..."}}
      ],
      "correct_option_ids": ["A"],
      "explanation": "..." or null,
      "needs_explanation_review": false,
      "topic": "..." or null,
      "subtopic": "..." or null,
      "complexity": "easy" or "medium" or "hard"
    }}
  ],
  "detected_answer_format": "A/B/C/D",
  "extraction_notes": "..."
}}"""

GENERATION_PROMPT = """You are an expert MCQ generation system for competitive exam preparation.

Generate {count} multiple-choice questions based on the provided content.

RULES:
1. Each question must have exactly 4 options (A, B, C, D) with one correct answer
2. Include a clear explanation for each question
3. All options must be plausible (competitive distractors, not obviously wrong)
4. Match the style and difficulty distribution of the provided example questions
5. Questions must be relevant to the topic/subtopic specified
6. Assign complexity: easy/medium/hard based on cognitive demand
7. Use Nepali language for content if the source material is in Nepali

Active skill instructions:
{skill_instructions}

Topic: {topic}
Subtopic: {subtopic}
Custom instruction: {custom_instruction}

Example approved questions for style reference:
{style_examples}

Source content to generate questions from:
{document_text}

Return ONLY valid JSON with the same structure as the extraction format."""

REGENERATION_PROMPT = """You are an expert MCQ regeneration system for competitive exam preparation.

Regenerate the following rejected questions based on admin feedback.

Admin feedback: {feedback}

RULES:
1. Address all feedback points specifically
2. Generate replacement questions for each rejected question
3. Maintain the same topic/subtopic as the rejected question
4. Each question must have 4 options (A, B, C, D) with one correct answer and explanation
5. Do NOT repeat the same question text

Active skill instructions:
{skill_instructions}

Style examples from approved questions:
{style_examples}

Rejected questions to replace:
{rejected_questions}

Source content:
{document_text}

Return ONLY valid JSON with the same structure as the extraction format."""


class MCQExtractionAgent:
    def __init__(self, db: AsyncSession, job_id: uuid.UUID, document: MCQDocument):
        self.db = db
        self.job_id = job_id
        self.document = document
        self.provider = get_provider("reasoning")

    async def process(self) -> MCQReviewBatch:
        await update_job(self.db, self.job_id, progress=5, step="Downloading source document")

        # Download file from R2
        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from sqlalchemy import select

        file_r = await self.db.execute(select(File).where(File.id == self.document.file_id))
        file_record = file_r.scalar_one_or_none()
        if not file_record:
            raise ValueError("Source file not found")

        file_bytes = get_r2().download_fileobj(file_record.r2_key)

        await update_job(self.db, self.job_id, progress=20, step="Extracting text from document")

        from app.processing.document_text import extract_text_from_bytes
        document_text = extract_text_from_bytes(file_bytes, file_record.mime_type)

        await update_job(self.db, self.job_id, progress=35, step="Retrieving active skill")
        skill_instructions = await self._get_skill()

        await update_job(self.db, self.job_id, progress=50, step="Extracting MCQs with AI")

        prompt = EXTRACTION_PROMPT.format(
            skill_instructions=skill_instructions,
            topic_hint=self.document.topic or "auto-detect",
            subtopic_hint=self.document.subtopic or "auto-detect",
            custom_instruction=self.document.custom_instruction or "none",
            document_text=document_text[:40000],
        )

        audit_ctx = {
            "db": self.db,
            "agent_type": "MCQExtractionAgent",
            "task_type": "mcq_extraction",
            "entity_type": "mcq_document",
            "entity_id": self.document.id,
        }

        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"AI extraction failed: {exc}") from exc

        await update_job(self.db, self.job_id, progress=75, step="Saving extracted questions")

        questions_data = result.get("questions", []) if isinstance(result, dict) else []

        batch = MCQReviewBatch(
            document_id=self.document.id,
            batch_type="extraction",
            status="in_review",
            total_questions=len(questions_data),
            job_id=self.job_id,
            created_by=self.document.created_by,
        )
        self.db.add(batch)
        await self.db.flush()

        for q_data in questions_data:
            options = q_data.get("options", [])
            if len(options) < 4:
                continue
            question = MCQQuestion(
                source_document_id=self.document.id,
                review_batch_id=batch.id,
                origin_type="uploaded_extracted",
                question_text=q_data.get("question_text", ""),
                options=options,
                correct_option_ids=q_data.get("correct_option_ids", []),
                explanation=q_data.get("explanation"),
                topic=q_data.get("topic") or self.document.topic,
                subtopic=q_data.get("subtopic") or self.document.subtopic,
                complexity=q_data.get("complexity", "medium"),
                status="draft",
            )
            self.db.add(question)

        self.document.processing_status = "completed"
        self.document.question_count = len(questions_data)

        await self.db.commit()
        await self.db.refresh(batch)

        await update_job(self.db, self.job_id, progress=100, step="Extraction complete")
        return batch

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "MCQExtractionAgent")
        except Exception:
            return "Extract all MCQs accurately. Normalize options to A/B/C/D. Preserve Nepali text exactly."


class MCQGenerationAgent:
    def __init__(self, db: AsyncSession, job_id: uuid.UUID, document: MCQDocument, count: int):
        self.db = db
        self.job_id = job_id
        self.document = document
        self.count = count
        self.provider = get_provider("reasoning")

    async def process(self) -> MCQReviewBatch:
        await update_job(self.db, self.job_id, progress=5, step="Downloading source content")

        from app.integrations.r2_client import get_r2
        from app.modules.files.models import File
        from sqlalchemy import select

        file_r = await self.db.execute(select(File).where(File.id == self.document.file_id))
        file_record = file_r.scalar_one_or_none()
        if not file_record:
            raise ValueError("Source file not found")

        file_bytes = get_r2().download_fileobj(file_record.r2_key)

        await update_job(self.db, self.job_id, progress=20, step="Extracting text from content")
        from app.processing.document_text import extract_text_from_bytes
        document_text = extract_text_from_bytes(file_bytes, file_record.mime_type)

        await update_job(self.db, self.job_id, progress=30, step="Retrieving style examples")
        style_examples = await self._get_style_examples()

        await update_job(self.db, self.job_id, progress=40, step="Retrieving active skill")
        skill_instructions = await self._get_skill()

        await update_job(self.db, self.job_id, progress=55, step="Generating MCQs with AI")

        prompt = GENERATION_PROMPT.format(
            count=self.count,
            skill_instructions=skill_instructions,
            topic=self.document.topic or "general",
            subtopic=self.document.subtopic or "general",
            custom_instruction=self.document.custom_instruction or "none",
            style_examples=style_examples,
            document_text=document_text[:30000],
        )

        audit_ctx = {
            "db": self.db,
            "agent_type": "MCQGenerationAgent",
            "task_type": "mcq_generation",
            "entity_type": "mcq_document",
            "entity_id": self.document.id,
        }

        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"AI generation failed: {exc}") from exc

        await update_job(self.db, self.job_id, progress=80, step="Saving generated questions")

        questions_data = result.get("questions", []) if isinstance(result, dict) else []

        batch = MCQReviewBatch(
            document_id=self.document.id,
            batch_type="generation",
            status="in_review",
            total_questions=len(questions_data),
            job_id=self.job_id,
            created_by=self.document.created_by,
        )
        self.db.add(batch)
        await self.db.flush()

        for q_data in questions_data:
            options = q_data.get("options", [])
            if len(options) < 4:
                continue
            question = MCQQuestion(
                source_document_id=self.document.id,
                review_batch_id=batch.id,
                origin_type="ai_generated",
                question_text=q_data.get("question_text", ""),
                options=options,
                correct_option_ids=q_data.get("correct_option_ids", []),
                explanation=q_data.get("explanation"),
                topic=q_data.get("topic") or self.document.topic,
                subtopic=q_data.get("subtopic") or self.document.subtopic,
                complexity=q_data.get("complexity", "medium"),
                status="draft",
            )
            self.db.add(question)

        self.document.processing_status = "completed"
        self.document.question_count = len(questions_data)

        await self.db.commit()
        await self.db.refresh(batch)
        await update_job(self.db, self.job_id, progress=100, step="Generation complete")
        return batch

    async def _get_style_examples(self) -> str:
        from sqlalchemy import select
        result = await self.db.execute(
            select(MCQQuestion)
            .where(MCQQuestion.status == "approved", MCQQuestion.topic == self.document.topic)
            .limit(8)
        )
        examples = result.scalars().all()
        if not examples:
            result = await self.db.execute(
                select(MCQQuestion).where(MCQQuestion.status == "approved").limit(8)
            )
            examples = result.scalars().all()

        if not examples:
            return "No style examples available yet. Generate diverse, well-structured questions."

        lines = []
        for ex in examples:
            lines.append(f"Q: {ex.question_text}")
            for opt in ex.options:
                correct = "✓" if opt["id"] in ex.correct_option_ids else " "
                lines.append(f"  [{correct}] {opt['id']}. {opt['text']}")
            lines.append(f"  Explanation: {ex.explanation or 'N/A'}")
            lines.append("")
        return "\n".join(lines)

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "MCQGenerationAgent")
        except Exception:
            return "Generate high-quality MCQs with competitive distractors and clear explanations."


class MCQRegenerationAgent:
    def __init__(self, db: AsyncSession, job_id: uuid.UUID, batch: MCQReviewBatch, feedback: str):
        self.db = db
        self.job_id = job_id
        self.batch = batch
        self.feedback = feedback
        self.provider = get_provider("reasoning")

    async def process(self) -> MCQReviewBatch:
        await update_job(self.db, self.job_id, progress=10, step="Collecting rejected questions")

        from sqlalchemy import select
        rejected_r = await self.db.execute(
            select(MCQQuestion).where(
                MCQQuestion.review_batch_id == self.batch.id,
                MCQQuestion.status == "rejected",
            )
        )
        rejected = rejected_r.scalars().all()

        if not rejected:
            await update_job(self.db, self.job_id, progress=100, step="No rejected questions found")
            return self.batch

        await update_job(self.db, self.job_id, progress=20, step="Loading source document")

        document_text = ""
        if self.batch.document_id:
            from app.modules.mcq.models import MCQDocument as MCQDoc
            from app.modules.files.models import File
            from app.integrations.r2_client import get_r2
            from app.processing.document_text import extract_text_from_bytes

            doc_r = await self.db.execute(select(MCQDoc).where(MCQDoc.id == self.batch.document_id))
            doc = doc_r.scalar_one_or_none()
            if doc and doc.file_id:
                file_r = await self.db.execute(select(File).where(File.id == doc.file_id))
                file_record = file_r.scalar_one_or_none()
                if file_record:
                    file_bytes = get_r2().download_fileobj(file_record.r2_key)
                    document_text = extract_text_from_bytes(file_bytes, file_record.mime_type)[:20000]

        await update_job(self.db, self.job_id, progress=35, step="Loading style examples")

        from app.modules.mcq.models import MCQQuestion
        style_r = await self.db.execute(
            select(MCQQuestion).where(MCQQuestion.status == "approved").limit(6)
        )
        style_qs = style_r.scalars().all()
        style_text = ""
        for ex in style_qs:
            style_text += f"Q: {ex.question_text}\n"
            for opt in ex.options:
                style_text += f"  {opt['id']}. {opt['text']}\n"
            style_text += f"  Answer: {ex.correct_option_ids}\n\n"

        rejected_text = ""
        for q in rejected:
            rejected_text += f"Question: {q.question_text}\nTopic: {q.topic}\nFeedback: {q.review_feedback}\n\n"

        skill_instructions = await self._get_skill()

        await update_job(self.db, self.job_id, progress=50, step="Regenerating with AI")

        prompt = REGENERATION_PROMPT.format(
            feedback=self.feedback,
            skill_instructions=skill_instructions,
            style_examples=style_text,
            rejected_questions=rejected_text,
            document_text=document_text,
        )

        audit_ctx = {
            "db": self.db,
            "agent_type": "MCQRegenerationAgent",
            "task_type": "mcq_regeneration",
            "entity_type": "mcq_review_batch",
            "entity_id": self.batch.id,
        }

        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"AI regeneration failed: {exc}") from exc

        await update_job(self.db, self.job_id, progress=80, step="Replacing rejected questions")

        questions_data = result.get("questions", []) if isinstance(result, dict) else []

        for i, (old_q, new_q_data) in enumerate(zip(rejected, questions_data)):
            options = new_q_data.get("options", [])
            if len(options) < 4:
                continue
            old_q.question_text = new_q_data.get("question_text", old_q.question_text)
            old_q.options = options
            old_q.correct_option_ids = new_q_data.get("correct_option_ids", old_q.correct_option_ids)
            old_q.explanation = new_q_data.get("explanation", old_q.explanation)
            old_q.complexity = new_q_data.get("complexity", old_q.complexity)
            old_q.status = "draft"
            old_q.review_feedback = None

        self.batch.status = "in_review"
        self.batch.rejection_feedback = self.feedback
        await self.db.commit()
        await self.db.refresh(self.batch)

        await update_job(self.db, self.job_id, progress=100, step="Regeneration complete")
        return self.batch

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "MCQRegenerationAgent")
        except Exception:
            return "Regenerate MCQs addressing admin feedback precisely. Improve quality significantly."
