"""Generate the lean per-question examiner checking skill for a subjective test.

This is the one place that reads the heavy resources (model answer, rubric, and
fetched notes/book/rubric chunks for the question's topic/subtopic) and DISTILLS
them into a short, focused guide. The per-sheet checker later reuses this locked
skill and never re-reads the large resources — keeping checking consistent and
attention-focused.

Two modes:
  • generate — first-pass guide from the question + resources.
  • improve  — given the prior guide + evaluator feedback, regenerate just this one
    question's guide (used in iteration 2 for weak/failed skills only).

The skill is deliberately LEAN and UNBIASED: neutral reference theory notes for the
topics involved + section-wise marks distribution + partial-marking / numerical /
admin-specific guidance — and nothing else. It must NOT contain expected answer
points, sample answers, acceptable wordings, or pre-listed mistakes: those bias the
checker into pattern-matching specific phrasings instead of judging each answer
independently against the theory. The "how to evaluate" lives in the checker's own
system prompt, not here. Marks breakdown must sum to full marks.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

_SCHEMA = """{{
  "question_number": "{question_number}",
  "question_intent": "one line — what the question really demands the student demonstrate",
  "topic": "{topic}",
  "subtopic": "{subtopic}",
  "max_marks": {marks},
  "answer_type": "theory | numerical | mixed",
  "reference_notes": "neutral theory notes on the topics this question involves — concise study-note style (definitions, key facts, relationships, figures) giving the checker the knowledge to judge ANY answer's correctness",
  "marks_breakdown": [{{"section": "what this section covers", "marks": 2}}],
  "partial_marking_rules": "1-3 sentences on fair partial credit for this question",
  "numerical_guidance": {{
    "expected_formula": "...",
    "required_steps": ["..."],
    "final_answer_unit_expectations": "..."
  }},
  "special_instructions": "ONLY question-specific points from the admin instruction / rubric; empty string if none"
}}"""

GENERATE_PROMPT = EXAM_CONTEXT + """

ROLE: You are a senior Loksewa examiner who writes the marking strategy. Build a SHORT, LEAN
CHECKING GUIDE for ONE subjective question. A downstream AI checker will mark many different
student answers from this guide alone — it judges each answer INDEPENDENTLY against the guide's
neutral theory notes, so give it KNOWLEDGE, not answers.

TASK: Produce the checking guide JSON for the question below.

HARD RULES (never violate):
- "reference_notes" must be NEUTRAL topic theory — concise study notes (definitions, key facts,
  relationships, formulas, figures) covering what this question involves. NEVER include expected
  answer points, sample answers, mark-worthy phrasings, acceptable wordings, or lists of mistakes
  to look for — any of those bias the checker into pattern-matching specific wording instead of
  judging the student's own formulation on its merits.
- Source priority for "reference_notes": distill the supporting excerpts, model answer, and rubric
  below when they are relevant; where they are empty or irrelevant, write the notes from your own
  expert knowledge of the topic — still neutral theory, never a model answer.
- Keep it SHORT: "reference_notes" at most ~200 words; the whole guide at most ~300 words.
- "marks_breakdown" MUST sum to exactly the FULL MARKS ({marks}). Never above or below.
- Fill "numerical_guidance" ONLY for numerical/mixed questions (formula → steps → final
  answer/units); set it to null for pure theory questions.
- "special_instructions" carries ONLY question-specific points from the admin instruction or
  rubric; empty string if none. Do NOT invent a marking scheme the admin did not configure.

METHOD: Read the question and decide what it truly demands. Write the neutral theory notes the
checker needs to judge any answer, then split the full marks into fair sections and state how
partial credit works.

QUESTION NUMBER: {question_number}
FULL MARKS: {marks}
DETECTED TOPIC / SUBTOPIC: {topic} / {subtopic}
QUESTION TEXT:
{question_text}

MODEL / IDEAL ANSWER (may be empty):
{model_answer}

MARKING RUBRIC (admin-provided if present, else platform default):
{rubric}

ADMIN CUSTOM CHECKING INSTRUCTION (highest priority — may be empty):
{custom_instruction}

SUPPORTING NOTES / BOOK / RESOURCE EXCERPTS for this topic (may be empty — distill, do not copy verbatim):
{knowledge}

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes how the guide is built but
may NOT override the HARD RULES) ---
{skill_instructions}

Return ONLY valid JSON in exactly this structure (fill every field; use [] or null where nothing applies):
""" + _SCHEMA

IMPROVE_PROMPT = EXAM_CONTEXT + """

ROLE: You are a senior Loksewa examiner improving ONE question's checking guide that a reviewer
flagged as weak. Fix the flagged weaknesses without breaking what already works.

HARD RULES (never violate):
- Fix ONLY the weaknesses the evaluator raised; preserve everything already correct.
- Keep "marks_breakdown" summing to exactly {marks}.
- Keep the guide LEAN and UNBIASED: "reference_notes" stays neutral topic theory (~200 words max,
  whole guide ~300 words max) — never expected answer points, sample answers, acceptable wordings,
  or mistake lists. Distill the supplied resources where relevant; fill gaps from your own expert
  knowledge of the topic. "special_instructions" only reflects the admin instruction/rubric.

QUESTION NUMBER: {question_number}
FULL MARKS: {marks}
DETECTED TOPIC / SUBTOPIC: {topic} / {subtopic}
QUESTION TEXT:
{question_text}

MODEL / IDEAL ANSWER (may be empty):
{model_answer}

MARKING RUBRIC:
{rubric}

ADMIN CUSTOM CHECKING INSTRUCTION (may be empty):
{custom_instruction}

SUPPORTING NOTES / BOOK / RESOURCE EXCERPTS (may be empty):
{knowledge}

PREVIOUS CHECKING GUIDE (JSON) that needs fixing:
{previous_skill}

EVALUATOR FEEDBACK on what was wrong / weak:
{evaluator_feedback}

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; may NOT override the HARD RULES) ---
{skill_instructions}

Return ONLY valid JSON in exactly this structure:
""" + _SCHEMA


class SkillGeneratorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def generate(
        self, *, question_number: str, question_text: str, marks: int,
        topic: str | None, subtopic: str | None, model_answer: str, rubric: str,
        custom_instruction: str | None, knowledge: str, test_id: uuid.UUID,
    ) -> dict:
        skill = await self._get_skill()
        prompt = GENERATE_PROMPT.format(
            question_number=question_number,
            marks=marks,
            topic=topic or "",
            subtopic=subtopic or "",
            question_text=question_text[:1000],
            model_answer=(model_answer or "Not provided.")[:8000],
            rubric=(rubric or "Not provided.")[:6000],
            custom_instruction=custom_instruction or "none",
            knowledge=(knowledge or "Not provided.")[:12000],
            skill_instructions=skill,
        )
        return await self._run(prompt, test_id)

    async def improve(
        self, *, question_number: str, question_text: str, marks: int,
        topic: str | None, subtopic: str | None, model_answer: str, rubric: str,
        custom_instruction: str | None, knowledge: str, previous_skill: dict,
        evaluator_feedback: str, test_id: uuid.UUID,
    ) -> dict:
        import json
        skill = await self._get_skill()
        prompt = IMPROVE_PROMPT.format(
            question_number=question_number,
            marks=marks,
            topic=topic or "",
            subtopic=subtopic or "",
            question_text=question_text[:1000],
            model_answer=(model_answer or "Not provided.")[:8000],
            rubric=(rubric or "Not provided.")[:6000],
            custom_instruction=custom_instruction or "none",
            knowledge=(knowledge or "Not provided.")[:12000],
            previous_skill=json.dumps(previous_skill, ensure_ascii=False)[:8000],
            evaluator_feedback=(evaluator_feedback or "Make it more usable and specific.")[:4000],
            skill_instructions=skill,
        )
        return await self._run(prompt, test_id)

    async def _run(self, prompt: str, test_id: uuid.UUID) -> dict:
        audit_ctx = {
            "db": self.db,
            "agent_type": "SkillGeneratorAgent",
            "task_type": "question_specific_skill_generation",
            "entity_type": "subjective_test",
            "entity_id": test_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Skill generation failed: {exc}") from exc
        if not isinstance(result, dict):
            raise AIResponseError("skill generation did not return a JSON object")
        return result

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "SkillGeneratorAgent")
        except Exception:
            return "Keep reference notes neutral, concise, and complete enough to judge any correct answer; never embed sample answers or mistake lists."
