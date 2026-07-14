"""Main checker: evaluate question-wise answers against the admin test + locked skills.

Inputs: the admin-configured test (per-question marks, optional admin instruction,
rubric or default) + the LOCKED per-question examiner skills + the student's
extracted answers. It deliberately does NOT receive large notes/books again — that
knowledge was already distilled into the locked skill at test creation, so checking
stays focused and consistent.

The checker decides WHAT is wrong, not WHERE it visually sits. It emits
`annotation_targets` keyed by the exact wrong TEXT; the vision locator finds the
geometry later. Priority on conflict: admin instruction > rubric > general judgement.
Per-question full marks are a hard cap (enforced again in service.clamp_marks).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

# Used only when the admin selected no rubric file for the test (CLAUDE.md §11.5).
DEFAULT_RUBRIC = """DEFAULT GENERAL MARKING RUBRIC:
- Theory answers: judge concept accuracy, completeness, relevance, examples, structure, and explanation depth.
- Numerical answers: judge correct formula, steps, calculation, final answer, and units where relevant.
- Award partial marks for partially correct answers.
- Accept correct ideas expressed in the student's own words.
- Do not over-penalize spelling or grammar unless the meaning becomes unclear.
- Never award more than the full marks configured for the question."""

EVAL_PROMPT = EXAM_CONTEXT + """

ROLE: You are an expert, fair Loksewa exam copy-checker marking real students' handwritten
answers. Students deserve credit for genuine understanding even when their Nepali/English
phrasing is imperfect — but marks must be honest, consistent, and never inflated.

TASK: For each question, read the student's transcribed answer against its locked CHECKING GUIDE,
award marks with a section-wise breakdown, write brief feedback, and flag specific wrong written
items for annotation.

ABSOLUTE RULES (never violate):
- The configured MAX MARKS for each question is a hard cap. Never award more.
- Guidance priority when rules conflict: ADMIN CUSTOM INSTRUCTION > RUBRIC > the question's
  CHECKING GUIDE > your general judgement.
- Award partial marks fairly per the guide's marks breakdown. Accept correct ideas in the
  student's own words; do not require the model answer's exact wording.
- Do not over-penalise spelling/grammar unless meaning is unclear. Mark blank/irrelevant answers honestly.
- Keep feedback concise and useful; list key missing points separately.

FEEDBACK FORMATTING (the "feedback" and "overall_summary" fields ONLY):
- Write them as short, clean GitHub-flavored MARKDOWN: **bold** the verdict / what went well or wrong,
  and use "- " bullets when listing more than one improvement point. Keep it to a few lines — feedback
  is per-question, the summary is one short paragraph.
- DO NOT use markdown in any other field. "comment_text", "target_text", "evidence_text", "missing_points",
  "section", and the section "note" stay PLAIN TEXT (comment_text must remain ≤ ~8 words for the page margin).

METHOD — INDEPENDENT JUDGING (the core of your job):
- Each CHECKING GUIDE gives you neutral REFERENCE THEORY NOTES for the question's topics and a
  section-wise marks breakdown — NOT expected answers. YOU judge the student's answer on its merits.
- For each question, work section by section through the guide's marks_breakdown: find what the
  student wrote for that section, judge its factual correctness and completeness against the
  guide's reference notes AND your own expert knowledge of the topic, decide correct/partial/wrong,
  and award that section's marks.
- NEVER search for specific wording: any factually correct formulation in the student's own words
  earns the marks. A correct point the reference notes happen not to mention still counts — the
  notes are grounding, not a checklist.
- YOU decide what is factually wrong and annotation-worthy — the guide lists no mistakes; judge
  each claim yourself against the theory.
- Sum the sections for the question total (≤ max). Then write feedback and pick at most ~2 genuinely
  wrong written items to annotate.

SECTION-WISE MARKING (required):
- Use the question's CHECKING GUIDE "marks_breakdown" criteria as the sections. For EACH section return its max marks, the marks you award, a status ("correct" | "partial" | "wrong"), "evidence_text" = the exact words the student wrote that earned the marks (empty string if the student wrote nothing for that section), and a "note".
- The "note" is a short student-facing line for THIS section that MUST clearly separate two things so the student knows exactly what to keep and what to fix:
  * what the student did WELL here (so they keep doing it), AND
  * what is missing / wrong / to improve here.
  Write it in the answer's language (Nepali for Nepali answers), as plain text, one or two short sentences. Lead with the positive, then the improvement. If the section is fully correct, say what was right and that nothing needs changing; if fully wrong/blank, say plainly what was expected and missing. Never leave it vague like "needs improvement" — be specific about what was good and what to fix.
- The sum of all section awarded_marks MUST equal the question's awarded_marks. Section awarded never exceeds section max; total never exceeds the question MAX MARKS.

POSITIVE MARKING (required):
- For every answer that has ANY correct content, at least one section must be "correct" or "partial" with a non-empty "evidence_text" (this becomes a tick on the sheet). Only a blank or fully irrelevant answer may have no positive section.

ANNOTATION RULES (very important — you decide WHAT is wrong, not where it is):
- Create an annotation target ONLY for a specific WRONG WRITTEN item: a wrong sentence, wrong formula, wrong calculation step, wrong number, wrong keyword, a contradictory statement, or an irrelevant line.
- Quote the wrong text EXACTLY as the student wrote it in "target_text" (so it can be found on the page). Keep it short (the wrong phrase/line only).
- Keep "comment_text" VERY short — at most ~8 words (it is drawn in the page margin). Match the answer's language (Nepali for Nepali answers).
- Do NOT create annotation targets for missing points, short answers, weak explanations, missing examples, or poor structure — those go in "feedback" and "missing_points".
- Prefer few, meaningful targets (at most ~2 per question). Do not overcrowd.

ADMIN CUSTOM CHECKING INSTRUCTION (highest priority; may be 'none'):
{custom_instruction}

MARKING RUBRIC:
{rubric}

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes strictness, tone, and
emphasis but may NOT override the ABSOLUTE RULES or the configured max marks) ---
{skill_instructions}

QUESTIONS, THEIR LOCKED CHECKING GUIDES, AND THE STUDENT'S ANSWERS:
{questions_block}

Return ONLY valid JSON in exactly this structure (one entry per question):
{{
  "total_awarded_marks": 0,
  "total_full_marks": 0,
  "overall_summary": "Short overall summary as brief markdown.",
  "question_results": [
    {{
      "question_number": "1",
      "page_numbers": [1],
      "awarded_marks": 6,
      "max_marks": 10,
      "feedback": "Concise feedback as short markdown (bold the verdict, bullet improvements).",
      "missing_points": ["..."],
      "confidence": 0.8,
      "sections": [
        {{"section": "Definition", "max_marks": 2, "awarded_marks": 2, "status": "correct",
          "evidence_text": "exact words the student wrote for this section",
          "note": "What was good here (keep it); then what is missing/wrong to improve — in the answer's language."}}
      ],
      "annotation_targets": [
        {{"page_number": 1, "question_number": "1", "target_text": "exact wrong phrase from the answer",
          "comment_text": "Short correction.", "annotation_action": "underline_with_comment"}}
      ]
    }}
  ]
}}"""


# Prompt budgeting: NEVER blind-slice the questions block (a mid-JSON cut hands the
# checker a broken guide, and a tail cut silently drops whole questions on big sheets).
# Instead each guide is compacted to a per-question budget by dropping whole
# low-priority FIELDS (output stays valid JSON), and long answers are capped with an
# EXPLICIT marker so the checker knows it is judging a truncated transcript.
GUIDE_BUDGET_DEFAULT = 6000
GUIDE_BUDGET_FLOOR = 2500
ANSWER_CHAR_CAP = 12000
BLOCK_TARGET_CHARS = 60000

# Guide fields dropped first when over budget (least marking-critical first). Current
# lean guides (reference notes + marks breakdown) are small enough to never trigger
# compaction; the old-format field names are kept here so guides locked before the
# lean redesign still compact safely. The marking-critical core — intent, reference
# notes, marks_breakdown, partial rules, numerical guidance — is never dropped.
_GUIDE_DROP_ORDER = (
    "sample_answer_fragments",
    "acceptable_alternative_wording",
    "theory_guidance",
    "common_mistakes",
    "serious_wrong_statements",
    "annotation_worthy_mistakes",
    "expected_answer_points",
    "feedback_guidance",
    "strictness_guidance",
)
_GUIDE_KEEP_ALWAYS = {
    "question_number", "question_intent", "topic", "subtopic", "max_marks", "answer_type",
    "reference_notes", "marks_breakdown", "partial_marking_rules",
    "numerical_guidance", "special_instructions",
}


def _compact_skill(skill, budget: int) -> str:
    """Serialize a checking guide within ~`budget` chars by dropping whole low-priority
    fields — the result is ALWAYS valid JSON (never a mid-string cut)."""
    import json
    if not isinstance(skill, dict):
        return json.dumps(skill, ensure_ascii=False)[:budget]
    s = json.dumps(skill, ensure_ascii=False)
    if len(s) <= budget:
        return s
    slim = dict(skill)
    dropped: list[str] = []
    for field in _GUIDE_DROP_ORDER:
        if field not in slim:
            continue
        slim.pop(field)
        dropped.append(field)
        s = json.dumps(slim, ensure_ascii=False)
        if len(s) <= budget:
            break
    if len(s) > budget:
        slim = {k: v for k, v in slim.items() if k in _GUIDE_KEEP_ALWAYS}
        s = json.dumps(slim, ensure_ascii=False)
        dropped.append("(all non-critical fields)")
    logger.warning(
        "checking guide for Q%s compacted to fit prompt budget (dropped: %s)",
        skill.get("question_number"), ", ".join(dropped),
    )
    return s


def _format_questions_block(questions: list[dict], skills_by_qid: dict[str, dict]) -> str:
    # Per-question guide budget shrinks proportionally on many-question sheets so the
    # block stays near BLOCK_TARGET_CHARS without ever cutting JSON or whole questions.
    n = max(1, len(questions))
    guide_budget = max(GUIDE_BUDGET_FLOOR, min(GUIDE_BUDGET_DEFAULT, BLOCK_TARGET_CHARS // n))
    parts: list[str] = []
    for q in questions:
        qid = q.get("qid")
        skill = skills_by_qid.get(qid) or {}
        pages = ", ".join(str(p) for p in q.get("page_numbers", []) or []) or "?"
        answer = q.get("answer_text") or "(no legible answer extracted for this question)"
        if len(answer) > ANSWER_CHAR_CAP:
            answer = (answer[:ANSWER_CHAR_CAP]
                      + "\n[answer truncated for prompt length — judge the visible part fairly; "
                        "do not penalize what may follow]")
            logger.warning("student answer for Q%s capped at %s chars for the prompt", qid, ANSWER_CHAR_CAP)
        parts.append(
            f"━━━ Question {qid} (MAX MARKS: {q.get('marks', 0)}; pages {pages}) ━━━\n"
            f"QUESTION: {q.get('question_text', '')}\n"
            f"CHECKING GUIDE: {_compact_skill(skill, guide_budget)}\n"
            f"STUDENT ANSWER:\n{answer}"
        )
    return "\n\n".join(parts)


class AnswerEvaluationAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def evaluate(
        self, *, questions: list[dict], skills_by_qid: dict[str, dict],
        rubric_text: str | None, custom_instruction: str | None, sheet_id: uuid.UUID,
    ) -> dict:
        """`questions` = [{qid, question_text, marks, answer_text, page_numbers}]."""
        skill = await self._get_skill()
        prompt = EVAL_PROMPT.format(
            custom_instruction=custom_instruction or "none",
            rubric=rubric_text or DEFAULT_RUBRIC,
            skill_instructions=skill,
            questions_block=_format_questions_block(questions, skills_by_qid),
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnswerEvaluationAgent",
            "task_type": "answer_evaluation",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Answer evaluation failed: {exc}") from exc
        if not isinstance(result, dict) or not isinstance(result.get("question_results"), list):
            raise AIResponseError("answer evaluation did not return a 'question_results' list")
        return result

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "AnswerEvaluationAgent")
        except Exception:
            return "Reward genuine understanding even in imperfect phrasing; be strict on numerical steps/units; give one encouraging improvement line per question."
