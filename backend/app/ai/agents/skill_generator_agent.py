"""Generate the detailed per-question examiner checking skill for a subjective test.

This is the one place that reads the heavy resources (model answer, rubric, and
fetched notes/book/rubric chunks for the question's topic/subtopic) and DISTILLS
them into a focused, practical examiner guide. The per-sheet checker later reuses
this locked skill and never re-reads the large resources — keeping checking
consistent and attention-focused.

Two modes:
  • generate — first-pass guide from the question + resources.
  • improve  — given the prior guide + evaluator feedback, regenerate just this one
    question's guide (used in iteration 2 for weak/failed skills only).

The skill is a CHECKING GUIDE, not a model answer: it must help mark many different
student answers fairly. Grounded only in the supplied inputs — never invents a
marking scheme the admin didn't configure. Marks breakdown must sum to full marks.
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
  "question_intent": "what the question is really asking the student to demonstrate",
  "topic": "{topic}",
  "subtopic": "{subtopic}",
  "max_marks": {marks},
  "answer_type": "theory | numerical | mixed",
  "expected_answer_points": ["concept/point the answer should contain", "..."],
  "sample_answer_fragments": ["short example of a mark-worthy phrasing", "..."],
  "acceptable_alternative_wording": ["valid variations / synonyms students may use", "..."],
  "marks_breakdown": [{{"point": "what earns marks", "marks": 2}}],
  "partial_marking_rules": "how to award partial credit fairly",
  "common_mistakes": ["typical incomplete/imperfect answers", "..."],
  "serious_wrong_statements": ["clearly wrong claims that must be penalized", "..."],
  "annotation_worthy_mistakes": ["the kinds of specific wrong written items worth a visual mark"],
  "feedback_guidance": "tone/length and what to mention in feedback",
  "strictness_guidance": "how strict to be (reflect admin instruction if any)",
  "theory_guidance": {{
    "core_concepts": ["..."],
    "explanation_depth_expected": "...",
    "mark_worthy_examples": ["..."],
    "structure_expectations": "...",
    "common_incomplete_answers": ["..."],
    "penalize_worthy_wrong_ideas": ["..."]
  }},
  "numerical_guidance": {{
    "expected_formula": "... or null",
    "required_steps": ["..."],
    "substitution_calculation_expectations": "...",
    "final_answer_unit_expectations": "...",
    "partial_marks": {{"formula": 0, "steps": 0, "calculation": 0, "final_answer": 0}},
    "common_calculation_mistakes": ["..."],
    "annotation_worthy_wrong_steps": ["..."]
  }}
}}"""

GENERATE_PROMPT = EXAM_CONTEXT + """

ROLE: You are a senior Loksewa examiner who writes the marking strategy. Build a detailed,
PRACTICAL examiner CHECKING GUIDE for ONE subjective question so that a downstream AI checker can
fairly and consistently mark many different student answers from it alone — without re-reading
the model answer, rubric, or notes.

TASK: Produce the checking guide JSON for the question below.

HARD RULES (never violate):
- Ground the guide ONLY in the question, model answer, rubric, admin instruction, and supporting
  excerpts below. Do NOT invent a marking scheme the admin did not configure.
- "marks_breakdown" MUST sum to exactly the FULL MARKS ({marks}). Never above or below.
- Make it a GUIDE for marking varied answers, not a single copied model answer. Capture
  acceptable alternative wordings and the range of correct approaches.
- Fill "theory_guidance" for theory questions, "numerical_guidance" for numerical (formula →
  steps → calculation → final answer/units), both for mixed; use null/[] for the inapplicable part.
- "annotation_worthy_mistakes" = ONLY specific WRONG written items (wrong sentence/formula/step/
  number/keyword, contradiction, irrelevant line). NEVER list "missing points" or "weak structure".

METHOD: Read the question and decide what it truly demands. Distil the model answer/rubric/notes
into the marks-worthy points, then design a fair partial-marking scheme and anticipate the common
imperfect answers and serious wrong claims a real Nepali student would write.

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
- Stay grounded in the supplied question/model answer/rubric/admin instruction/notes; invent nothing.

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
            question_text=question_text[:6000],
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
            question_text=question_text[:6000],
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
            return "Make guides concrete enough that a checker never has to guess; spell out acceptable Nepali phrasings and partial-credit thresholds."
