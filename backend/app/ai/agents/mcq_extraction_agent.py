import logging
import random
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError
from app.modules.jobs.service import update_job
from app.modules.mcq.models import MCQDocument, MCQReviewBatch, MCQQuestion
from app.modules.mcq.schemas import normalize_options

logger = logging.getLogger(__name__)

_VALID_COMPLEXITY = {"easy", "medium", "hard"}

EXTRACTION_PROMPT = EXAM_CONTEXT + """

ROLE: You are a meticulous question-bank digitiser. Admins upload real exam/practice MCQ
papers and you transcribe every question into clean structured data — faithfully, never
"improving" or inventing.

TASK: Extract ALL multiple-choice questions from the document below, exactly as written.

HARD RULES (never violate):
- Transcribe only what is on the page. Do NOT add, reword, correct, or invent questions,
  options, answers, or explanations. Preserve Devanagari and all wording verbatim.
- Each question must have EXACTLY 4 options normalised to IDs A, B, C, D in their original order.
- The source may mark the correct answer in any format (A/B/C/D, क/ख/ग/घ, 1/2/3/4, १/२/३/४, or
  a circled/ticked option). Detect it and map it to A/B/C/D in correct_option_ids.
- Take the explanation only from the source. If none is present, set explanation to null and
  needs_explanation_review to true — never write your own.

METHOD: First scan the whole document to learn its layout and answer-key convention (inline,
answer key at the end, etc.). Then walk question by question, matching each to its correct
option and explanation. Assign complexity from cognitive demand — definition/recall = "easy",
application = "medium", multi-step reasoning/analysis = "hard". Assign topic/subtopic only when
the context makes it unambiguous; otherwise leave null.

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes emphasis and judgement
but may NOT override the HARD RULES) ---
{skill_instructions}

INPUTS:
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

def _extract_question_text(q_data: dict) -> str:
    """Return question text regardless of which key the AI used."""
    for key in ("question_text", "question", "q", "text", "stem", "question_stem", "problem"):
        val = q_data.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # Log the actual keys returned so mismatches are visible in worker logs
    logger.warning("Could not extract question_text. Actual keys in q_data: %s", list(q_data.keys()))
    return ""


def _require_questions_list(result: object) -> list:
    """Validate the AI returned the expected `{"questions": [...]}` shape.

    Raises AIResponseError (→ job fails with a readable message) instead of
    silently coercing bad output into an empty list, which previously produced
    a 'completed' job with zero questions and no explanation.
    """
    if not isinstance(result, dict):
        raise AIResponseError("model response was not a JSON object")
    questions = result.get("questions")
    if not isinstance(questions, list):
        raise AIResponseError("model response did not include a 'questions' list")
    return questions


def _normalize_question(q_data: object) -> tuple[dict | None, str | None]:
    """Validate and normalize one question entry.

    Returns (fields, None) when usable, or (None, reason) when it must be
    skipped. The reason is surfaced to the admin via the job's output_reference
    so skipped questions are visible, not silently dropped.
    """
    if not isinstance(q_data, dict):
        return None, "entry was not an object"
    question_text = _extract_question_text(q_data)
    if not question_text:
        return None, "missing question text"
    options = normalize_options(q_data.get("options", []))
    if len(options) < 4:
        return None, f"only {len(options)} option(s) after normalization"
    correct = q_data.get("correct_option_ids") or []
    if not isinstance(correct, list) or not correct:
        return None, "missing correct answer"
    complexity = q_data.get("complexity", "medium")
    if complexity not in _VALID_COMPLEXITY:
        complexity = "medium"
    return {
        "question_text": question_text,
        "options": options,
        "correct_option_ids": correct,
        "explanation": q_data.get("explanation"),
        "topic": q_data.get("topic"),
        "subtopic": q_data.get("subtopic"),
        "complexity": complexity,
    }, None


def _normalize_batch(questions_data: list) -> tuple[list[dict], list[str]]:
    """Split a raw question list into (usable fields, skip reasons)."""
    normalized: list[dict] = []
    skipped: list[str] = []
    for q_data in questions_data:
        fields, reason = _normalize_question(q_data)
        if fields is None:
            skipped.append(reason or "invalid")
        else:
            normalized.append(fields)
    return normalized, skipped


_OPTION_IDS = ["A", "B", "C", "D"]


def _q_get(q, key):
    return q[key] if isinstance(q, dict) else getattr(q, key)


def _q_set(q, key, value) -> None:
    if isinstance(q, dict):
        q[key] = value
    else:
        setattr(q, key, value)


def balance_answer_positions(questions: list) -> None:
    """Spread the correct-answer position evenly over A/B/C/D across a batch.

    LLMs strongly bias the correct option toward the first position, so prompt
    instructions alone do not give a uniform distribution. This deterministically
    re-orders each question's options so the correct answer lands on a balanced,
    randomised set of positions (equal counts of A/B/C/D, shuffled order). Only the
    option ORDER and A/B/C/D labels change — the option texts and which text is
    correct are untouched, and `correct_option_ids` is updated to the new label.

    Works on both dict records (extraction/generation) and ORM MCQQuestion rows
    (regeneration). Only standard single-correct, 4-distinct-option questions are
    touched; anything unusual is left exactly as produced. Explanations must refer
    to the answer by content, not letter (enforced in the prompts), so re-labelling
    is safe.
    """
    eligible: list = []
    for q in questions:
        opts = _q_get(q, "options") or []
        correct = _q_get(q, "correct_option_ids") or []
        if len(opts) != 4 or len(correct) != 1:
            continue
        ids = [o.get("id") for o in opts if isinstance(o, dict)]
        if len(ids) == 4 and len(set(ids)) == 4 and correct[0] in ids:
            eligible.append(q)

    if not eligible:
        return

    # Balanced target positions (each of 0..3 appears ~n/4 times), randomised order.
    targets = [i % 4 for i in range(len(eligible))]
    random.shuffle(targets)

    for q, target in zip(eligible, targets):
        opts = _q_get(q, "options")
        correct_id = _q_get(q, "correct_option_ids")[0]
        correct_opt = next(o for o in opts if o.get("id") == correct_id)
        distractors = [o for o in opts if o.get("id") != correct_id]

        ordered = list(distractors)
        ordered.insert(target, correct_opt)  # place the correct option at the target slot

        new_opts = [
            {"id": _OPTION_IDS[pos], "label": _OPTION_IDS[pos], "text": str(o.get("text", ""))}
            for pos, o in enumerate(ordered)
        ]
        _q_set(q, "options", new_opts)
        _q_set(q, "correct_option_ids", [_OPTION_IDS[target]])


def _extraction_output(saved: int, skipped: list[str], total_returned: int, batch_id) -> dict:
    """Job output_reference so the admin can see how many questions were saved
    vs. skipped (and why), instead of silently losing malformed entries."""
    out: dict = {
        "batch_id": str(batch_id),
        "saved": saved,
        "skipped": len(skipped),
        "total_returned": total_returned,
    }
    if skipped:
        out["skip_reasons"] = skipped[:20]
    return out



KNOWLEDGE_DOC_TYPES = [
    ("notes", "Notes"),
    ("book_content", "Book Content"),
    ("handout", "Handout"),
    ("reference_material", "Reference Material"),
]

CHUNKS_PER_TYPE = 25


async def _fetch_knowledge_by_type(
    db: AsyncSession,
    topics: set[str],
    subtopics: set[str],
) -> str:
    """
    Fetch up to CHUNKS_PER_TYPE chunks from each knowledge document_type
    whose topic or subtopic matches the given sets.
    Returns a formatted string with a labeled section per document type.
    """
    if not topics and not subtopics:
        return "No matching knowledge content found for this document's topics."

    from sqlalchemy import select, or_
    from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument

    topic_filter = KnowledgeChunk.topic.in_(topics) if topics else False
    subtopic_filter = KnowledgeChunk.subtopic.in_(subtopics) if subtopics else False

    sections: list[str] = []

    for doc_type, type_label in KNOWLEDGE_DOC_TYPES:
        stmt = (
            select(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(
                KnowledgeDocument.content_usage_type == "objective",
                KnowledgeDocument.processing_status == "completed",
                KnowledgeDocument.document_type == doc_type,
                or_(topic_filter, subtopic_filter),
            )
            .order_by(KnowledgeChunk.topic, KnowledgeChunk.chunk_index)
            .limit(CHUNKS_PER_TYPE)
        )
        result = await db.execute(stmt)
        chunks = result.scalars().all()

        if not chunks:
            continue

        lines: list[str] = [f"━━━ {type_label} ━━━"]
        for chunk in chunks:
            label_parts = []
            if chunk.topic:
                label_parts.append(f"Topic: {chunk.topic}")
            if chunk.subtopic:
                label_parts.append(f"Subtopic: {chunk.subtopic}")
            label = " | ".join(label_parts) if label_parts else "General"
            lines.append(f"[{label}]\n{chunk.content}")

        sections.append("\n\n".join(lines))

    return "\n\n".join(sections) if sections else "No matching knowledge content found for this document's topics."


TOPIC_DETECTION_PROMPT = """TASK: Identify which of the listed syllabus topics/subtopics the document below
meaningfully covers, so the right knowledge can be fetched to enrich question generation.

HARD RULES:
- Choose ONLY from the exact strings in the list below. Never invent, paraphrase, or merge names.
- Include a topic/subtopic only when the document substantively covers it (more than a passing
  mention). When nothing clearly matches, return empty arrays — do not guess.

Available objective syllabus topics and subtopics:
{syllabus_topics}

Document content:
{document_text}

Return ONLY valid JSON:
{{
  "covered_topics": ["exact topic string from the list above", ...],
  "covered_subtopics": ["exact subtopic string from the list above", ...]
}}"""

GENERATION_PROMPT = EXAM_CONTEXT + """

ROLE: You are an expert Loksewa/banking exam question writer. You author original,
exam-quality MCQs that a real Public Service Commission paper-setter would be proud of —
testing genuine understanding, not trivia.

TASK: Write {count} original multiple-choice questions grounded in the SOURCE DOCUMENT below.

HARD RULES (never violate):
- GROUNDING: Build questions only from facts in the SOURCE DOCUMENT. The KNOWLEDGE CONTEXT is
  enrichment only — use it to verify facts, sharpen distractors, and deepen explanations, but
  NEVER create a question whose content appears only there. If the two conflict, trust the
  SOURCE DOCUMENT.
- SELF-CONTAINED — NEVER REFERENCE THE SOURCE: every question must read as a standalone exam
  question answerable from subject knowledge alone. Forbidden are phrases like "according to
  the document", "as per the text", "स्रोत दस्तावेजअनुसार", "दिइएको अनुच्छेदअनुसार", "passage मा
  उल्लेख भएअनुसार", "उपरोक्त सामग्रीअनुसार", or any reference to a document/passage/text/material.
  Write "What is X?" — never "According to the document, what is X?".
- Each question has exactly 4 options (A, B, C, D), exactly one correct, plus a clear explanation.
- No duplicate or near-duplicate stems; spread difficulty across easy/medium/hard.

DISTRACTOR DESIGN (this is what makes or breaks question quality — treat it as critical):
- The 3 wrong options must be EXPERT TRAPS, deliberately chosen to confuse a half-prepared student,
  exactly as a human Loksewa paper-setter would design them. They must be the SAME TYPE, scale, and
  format as the correct answer, and individually plausible.
- For NUMERICAL/quantity answers, distractors must be NEAR the correct value and look like real
  competing figures — e.g. the previously-quoted official value, a common rounding, transposed or
  off-by-one digits, or a related statistic. Keep identical units and number formatting.
    · Worked example (study the pattern, do NOT reuse this content): for "नेपालको कुल क्षेत्रफल कति हो?"
      the key is 1,47,516 वर्ग कि.मि.; strong distractors are 1,47,181 वर्ग कि.मि. (the older official
      figure), 1,48,516 वर्ग कि.मि., and 1,41,516 वर्ग कि.मि. — all close, same format, genuinely
      confusing. WEAK/forbidden: 50,000 वर्ग कि.मि. or 9,84,000 वर्ग कि.मि. (obviously wrong by scale).
- For CONCEPT/term answers, use closely-related terms, adjacent categories, common misconceptions, or
  swapped definitions — things a student who studied superficially would actually pick.
- Every distractor must be DEFENSIBLY wrong (factually incorrect, not a second correct answer), but
  never obviously wrong, never filler, never absurd, and never a throwaway "none of the above" unless
  the source genuinely uses it.

ANSWER PLACEMENT & EXPLANATION:
- Do NOT always put the correct answer first. Vary which option (A/B/C/D) is correct across the set so
  it is roughly evenly spread — never a guessable pattern. (The system also re-balances positions, so
  also keep each distractor sensible in ANY position.)
- In the explanation, refer to the answer by its CONTENT/VALUE, never by its option letter (write
  "क्षेत्रफल 1,47,516 वर्ग कि.मि. हो …" not "विकल्प A सही हो"), because option order is randomised after
  generation. State why the key is correct and, where useful, why a tempting distractor is wrong.

METHOD: Pull the exam-worthy facts/relationships from the source. For each, write a crisp,
unambiguous stem, decide the correct answer, then deliberately engineer 3 confusing same-type traps
per the DISTRACTOR DESIGN rules above. Mirror the style/difficulty of the example questions and the
source language.

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes emphasis, difficulty, and
style but may NOT override the HARD RULES) ---
{skill_instructions}

INPUTS:
Topic: {topic}
Subtopic: {subtopic}
Custom instruction: {custom_instruction}

Example approved questions for style reference:
{style_examples}

━━━ KNOWLEDGE CONTEXT (enrichment only — do not generate questions solely from this) ━━━
{knowledge_context}
━━━ END KNOWLEDGE CONTEXT ━━━

━━━ SOURCE DOCUMENT (generate all questions from this) ━━━
{document_text}
━━━ END SOURCE DOCUMENT ━━━

Return ONLY valid JSON in exactly this structure — options MUST be a list, never a dict:
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
      "explanation": "...",
      "topic": "..." or null,
      "subtopic": "..." or null,
      "complexity": "easy" or "medium" or "hard"
    }}
  ]
}}"""

REGENERATION_PROMPT = EXAM_CONTEXT + """

ROLE: You are an expert Loksewa/banking exam question writer fixing questions an admin rejected.
Treat the rejection feedback as the priority brief.

TASK: Produce one improved replacement for each rejected question below, fully addressing the
admin's feedback.

Admin feedback (priority brief — address every point): {feedback}

HARD RULES (never violate):
- Each replacement must concretely fix what the feedback objected to, and keep the SAME
  topic/subtopic as the question it replaces.
- GROUNDING: build replacements from the SOURCE DOCUMENT only; KNOWLEDGE CONTEXT is enrichment
  for quality/explanations, never the sole basis of a question.
- SELF-CONTAINED — NEVER REFERENCE THE SOURCE: no "according to the document", "as per the
  text", "स्रोत दस्तावेजअनुसार", "दिइएको अनुच्छेदअनुसार", or any reference to a
  document/passage/text/material. Write direct standalone exam questions.
- Exactly 4 options (A, B, C, D), one correct, with a clear explanation. Do NOT reuse a rejected
  question's wording.

DISTRACTOR DESIGN (critical to quality):
- The 3 wrong options must be EXPERT TRAPS like a human Loksewa paper-setter writes — same TYPE,
  scale, and format as the correct answer, and individually plausible.
- For numerical answers, distractors must sit NEAR the correct value (the older official figure, a
  common rounding, transposed/off-by-one digits, a related statistic) with identical units/format —
  never wrong by an obvious scale. For concept answers, use closely-related terms, adjacent
  categories, or common misconceptions.
- Every distractor is defensibly wrong but never obviously wrong, filler, or absurd.

ANSWER PLACEMENT & EXPLANATION:
- Do not default the correct answer to option A; vary the correct position. Refer to the answer by
  its CONTENT/VALUE in the explanation, never by option letter (option order is randomised after
  generation).

METHOD: Read each rejected question and its feedback, diagnose the specific weakness (ambiguous
stem, weak/obvious distractors, wrong/missing explanation, off-topic, too easy/hard, answer always
in the same position), then rewrite to remove it while engineering confusing same-type traps and a
crisp stem.

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes emphasis and style but may
NOT override the HARD RULES or the admin feedback) ---
{skill_instructions}

INPUTS:
Style examples from approved questions:
{style_examples}

Rejected questions to replace:
{rejected_questions}

━━━ KNOWLEDGE CONTEXT (enrichment only) ━━━
{knowledge_context}
━━━ END KNOWLEDGE CONTEXT ━━━

━━━ SOURCE DOCUMENT ━━━
{document_text}
━━━ END SOURCE DOCUMENT ━━━

Return ONLY valid JSON in exactly this structure — options MUST be a list, never a dict:
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
      "explanation": "...",
      "topic": "..." or null,
      "subtopic": "..." or null,
      "complexity": "easy" or "medium" or "hard"
    }}
  ]
}}"""


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

        questions_data = _require_questions_list(result)
        normalized, skipped = _normalize_batch(questions_data)

        # batch + questions + document update commit together as one transaction.
        batch = MCQReviewBatch(
            document_id=self.document.id,
            batch_type="extraction",
            status="in_review",
            total_questions=len(normalized),
            job_id=self.job_id,
            created_by=self.document.created_by,
        )
        self.db.add(batch)
        await self.db.flush()

        for f in normalized:
            self.db.add(MCQQuestion(
                source_document_id=self.document.id,
                review_batch_id=batch.id,
                origin_type="uploaded_extracted",
                question_text=f["question_text"],
                options=f["options"],
                correct_option_ids=f["correct_option_ids"],
                explanation=f["explanation"],
                topic=f["topic"] or self.document.topic,
                subtopic=f["subtopic"] or self.document.subtopic,
                complexity=f["complexity"],
                status="draft",
            ))

        self.document.processing_status = "completed"
        self.document.question_count = len(normalized)

        await self.db.commit()
        await self.db.refresh(batch)

        await update_job(
            self.db, self.job_id, progress=100, step="Extraction complete",
            output=_extraction_output(len(normalized), skipped, len(questions_data), batch.id),
        )
        return batch

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "MCQExtractionAgent")
        except Exception:
            return "Favour exact fidelity over tidiness; when the answer key is ambiguous, mark needs_explanation_review rather than guessing."


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

        await update_job(self.db, self.job_id, progress=30, step="Detecting covered topics")
        covered_topics, covered_subtopics = await self._detect_covered_topics(document_text)

        await update_job(self.db, self.job_id, progress=42, step="Fetching knowledge layer enrichment")
        knowledge_context = await self._fetch_knowledge_context(covered_topics, covered_subtopics)

        await update_job(self.db, self.job_id, progress=52, step="Retrieving style examples")
        style_examples = await self._get_style_examples()

        await update_job(self.db, self.job_id, progress=60, step="Retrieving active skill")
        skill_instructions = await self._get_skill()

        await update_job(self.db, self.job_id, progress=68, step="Generating MCQs with AI")

        prompt = GENERATION_PROMPT.format(
            count=self.count,
            skill_instructions=skill_instructions,
            topic=self.document.topic or "general",
            subtopic=self.document.subtopic or "general",
            custom_instruction=self.document.custom_instruction or "none",
            style_examples=style_examples,
            knowledge_context=knowledge_context,
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

        await update_job(self.db, self.job_id, progress=88, step="Saving generated questions")

        questions_data = _require_questions_list(result)
        normalized, skipped = _normalize_batch(questions_data)

        # A generation request explicitly asked for N questions; producing none
        # usable is a failure, not a silently-completed empty batch.
        if not normalized:
            raise AIResponseError(
                f"generation produced no usable questions out of {len(questions_data)} returned"
            )

        # Spread the correct option evenly across A/B/C/D (LLMs cluster it on A).
        balance_answer_positions(normalized)

        batch = MCQReviewBatch(
            document_id=self.document.id,
            batch_type="generation",
            status="in_review",
            total_questions=len(normalized),
            job_id=self.job_id,
            created_by=self.document.created_by,
        )
        self.db.add(batch)
        await self.db.flush()

        for f in normalized:
            self.db.add(MCQQuestion(
                source_document_id=self.document.id,
                review_batch_id=batch.id,
                origin_type="ai_generated",
                question_text=f["question_text"],
                options=f["options"],
                correct_option_ids=f["correct_option_ids"],
                explanation=f["explanation"],
                topic=f["topic"] or self.document.topic,
                subtopic=f["subtopic"] or self.document.subtopic,
                complexity=f["complexity"],
                status="draft",
            ))

        self.document.processing_status = "completed"
        self.document.question_count = len(normalized)

        await self.db.commit()
        await self.db.refresh(batch)
        await update_job(
            self.db, self.job_id, progress=100, step="Generation complete",
            output=_extraction_output(len(normalized), skipped, len(questions_data), batch.id),
        )
        return batch

    async def _detect_covered_topics(self, document_text: str) -> tuple[list[str], list[str]]:
        """Identify which objective syllabus topics this document covers."""
        from sqlalchemy import select
        from app.modules.syllabus.models import SyllabusItem, SyllabusType

        rows = await self.db.execute(
            select(SyllabusItem).where(
                SyllabusItem.syllabus_type == SyllabusType.objective,
                SyllabusItem.is_active == True,
            )
        )
        items = rows.scalars().all()

        if not items:
            return [], []

        # Build a compact topic/subtopic listing for the prompt
        topic_lines: list[str] = []
        seen_topics: set[str] = set()
        for item in items:
            if item.topic and item.topic not in seen_topics:
                topic_lines.append(f"Topic: {item.topic}")
                seen_topics.add(item.topic)
            if item.subtopic:
                topic_lines.append(f"  Subtopic: {item.subtopic}")

        syllabus_topics_text = "\n".join(topic_lines) if topic_lines else "No syllabus topics defined."

        prompt = TOPIC_DETECTION_PROMPT.format(
            syllabus_topics=syllabus_topics_text,
            document_text=document_text[:15000],
        )

        audit_ctx = {
            "db": self.db,
            "agent_type": "MCQGenerationAgent",
            "task_type": "topic_detection",
            "entity_type": "mcq_document",
            "entity_id": self.document.id,
        }

        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
            covered_topics = result.get("covered_topics", []) if isinstance(result, dict) else []
            covered_subtopics = result.get("covered_subtopics", []) if isinstance(result, dict) else []
            # Validate against known topics to prevent hallucinated strings
            valid_topics = {item.topic for item in items if item.topic}
            valid_subtopics = {item.subtopic for item in items if item.subtopic}
            covered_topics = [t for t in covered_topics if t in valid_topics]
            covered_subtopics = [s for s in covered_subtopics if s in valid_subtopics]
            return covered_topics, covered_subtopics
        except Exception as exc:
            logger.warning("Topic detection failed, proceeding without enrichment: %s", exc)
            return [], []

    async def _fetch_knowledge_context(
        self,
        covered_topics: list[str],
        covered_subtopics: list[str],
    ) -> str:
        """Fetch 25 chunks per document type for the detected topics."""
        return await _fetch_knowledge_by_type(
            self.db,
            topics=set(covered_topics),
            subtopics=set(covered_subtopics),
        )

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
            return "Favour application over rote recall; make every distractor a plausible Loksewa-style trap, not filler."


class MCQRegenerationAgent:
    def __init__(self, db: AsyncSession, job_id: uuid.UUID, batch: MCQReviewBatch, feedback: str):
        self.db = db
        self.job_id = job_id
        self.batch = batch
        # Capture the batch UUID as a plain Python value NOW, before any DB
        # commit can expire the ORM object and make self.batch.id a lazy-load.
        self._batch_id: uuid.UUID = batch.id
        self.feedback = feedback
        self.provider = get_provider("reasoning")

    async def process(self) -> MCQReviewBatch:
        from app.modules.jobs.models import JobStatus

        await update_job(self.db, self.job_id, progress=10, step="Collecting rejected questions")

        from sqlalchemy import select
        rejected_r = await self.db.execute(
            select(MCQQuestion).where(
                MCQQuestion.review_batch_id == self._batch_id,
                MCQQuestion.status == "rejected",
            )
        )
        rejected = rejected_r.scalars().all()

        logger.info(
            "Regeneration: batch_id=%s — found %d rejected question(s)",
            self._batch_id, len(rejected),
        )

        if not rejected:
            logger.warning(
                "Regeneration: batch_id=%s has 0 rejected questions; "
                "they may have already been regenerated in a previous run.",
                self._batch_id,
            )
            await update_job(
                self.db, self.job_id,
                status=JobStatus.completed,
                progress=100,
                step="No rejected questions found — they may already have been regenerated",
            )
            return self.batch

        await update_job(self.db, self.job_id, progress=20, step="Loading source document")

        document_text = ""
        doc_topic: str | None = None
        doc_subtopic: str | None = None

        # Re-load the batch to get a fresh, non-expired reference after the commits above
        fresh_batch_r = await self.db.execute(select(MCQReviewBatch).where(MCQReviewBatch.id == self._batch_id))
        self.batch = fresh_batch_r.scalar_one_or_none() or self.batch

        if self.batch.document_id:
            from app.modules.mcq.models import MCQDocument as MCQDoc
            from app.modules.files.models import File
            from app.integrations.r2_client import get_r2
            from app.processing.document_text import extract_text_from_bytes

            doc_r = await self.db.execute(select(MCQDoc).where(MCQDoc.id == self.batch.document_id))
            doc = doc_r.scalar_one_or_none()
            if doc:
                doc_topic = doc.topic
                doc_subtopic = doc.subtopic
                if doc.file_id:
                    file_r = await self.db.execute(select(File).where(File.id == doc.file_id))
                    file_record = file_r.scalar_one_or_none()
                    if file_record:
                        file_bytes = get_r2().download_fileobj(file_record.r2_key)
                        document_text = extract_text_from_bytes(file_bytes, file_record.mime_type)[:20000]

        await update_job(self.db, self.job_id, progress=35, step="Fetching knowledge enrichment")
        knowledge_context = await self._fetch_knowledge_context(rejected, doc_topic, doc_subtopic)

        await update_job(self.db, self.job_id, progress=45, step="Loading style examples")

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

        await update_job(self.db, self.job_id, progress=55, step="Regenerating with AI")

        prompt = REGENERATION_PROMPT.format(
            feedback=self.feedback,
            skill_instructions=skill_instructions,
            style_examples=style_text,
            rejected_questions=rejected_text,
            knowledge_context=knowledge_context,
            document_text=document_text,
        )

        audit_ctx = {
            "db": self.db,
            "agent_type": "MCQRegenerationAgent",
            "task_type": "mcq_regeneration",
            "entity_type": "mcq_review_batch",
            "entity_id": self._batch_id,
        }

        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"AI regeneration failed: {exc}") from exc

        await update_job(self.db, self.job_id, progress=85, step="Replacing rejected questions")

        questions_data = _require_questions_list(result)

        replaced = 0
        skipped: list[str] = []
        replaced_questions: list = []
        for old_q, new_q_data in zip(rejected, questions_data):
            fields, reason = _normalize_question(new_q_data)
            if fields is None:
                skipped.append(reason or "invalid")
                continue
            old_q.question_text = fields["question_text"]
            old_q.options = fields["options"]
            old_q.correct_option_ids = fields["correct_option_ids"]
            old_q.explanation = fields["explanation"]
            old_q.complexity = fields["complexity"]
            old_q.status = "draft"
            old_q.review_feedback = None
            replaced += 1
            replaced_questions.append(old_q)

        if replaced == 0:
            raise AIResponseError(
                f"regeneration produced no usable replacements out of {len(questions_data)} returned"
            )

        # Spread the correct option evenly across A/B/C/D (LLMs cluster it on A).
        balance_answer_positions(replaced_questions)

        self.batch.status = "in_review"
        self.batch.rejection_feedback = self.feedback
        await self.db.commit()
        await self.db.refresh(self.batch)

        await update_job(
            self.db, self.job_id,
            status=JobStatus.completed,
            progress=100,
            step="Regeneration complete",
            output={
                "batch_id": str(self._batch_id),
                "rejected": len(rejected),
                "replaced": replaced,
                "skipped": len(skipped),
                **({"skip_reasons": skipped[:20]} if skipped else {}),
            },
        )
        return self.batch

    async def _fetch_knowledge_context(
        self,
        rejected_questions: list,
        doc_topic: str | None,
        doc_subtopic: str | None,
    ) -> str:
        """Fetch 25 chunks per document type for topics present in the rejected questions."""
        topics = {q.topic for q in rejected_questions if q.topic}
        subtopics = {q.subtopic for q in rejected_questions if q.subtopic}
        if doc_topic:
            topics.add(doc_topic)
        if doc_subtopic:
            subtopics.add(doc_subtopic)
        return await _fetch_knowledge_by_type(self.db, topics=topics, subtopics=subtopics)

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "MCQRegenerationAgent")
        except Exception:
            return "Treat the rejection feedback as the brief; fix the exact weakness it names rather than making cosmetic edits."
