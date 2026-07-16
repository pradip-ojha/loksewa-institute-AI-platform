"""Stage C of MCQ extraction (CLAUDE.md §9.1): assign topic/subtopic to already-extracted
questions, scoped to ONE chapter at a time so a topic from the wrong chapter can never be
attached. A separate RECOVERY mode handles questions the extractor left chapter-null: it is
given the FULL tree and assigns chapter + topic in one go.

Mechanical routing (pick from a fixed list) → `get_provider("thinking")` (gpt-5), and it is
deliberately NOT skill-tunable (like the syllabus-import agent) — there is no `_get_skill`
and it is absent from `_DEFAULT_SKILLS`. The caller validates every returned label against
the live syllabus (`video.service.resolve_syllabus_labels`); this agent only proposes.

Batch call: each question carries a `Ref: R<n>` code the model must echo in `ref`, so the
returned assignments map back to the right question even if the model reorders or drops one.
Alignment mirrors `mcq_extraction_agent._align_replacements` — an unknown/duplicate/missing
ref means that question keeps a null topic (never a mis-assignment).
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)


SCOPED_PROMPT = """ROLE: You place already-extracted MCQs into their topic within ONE fixed chapter, so each
question is filed under the right topic for retrieval.

TASK: For every question below, pick the single best topic (and subtopic when clear) from the
chapter's syllabus — nothing else.

HARD RULES (never violate):
- Choose topic and subtopic ONLY from the list below — copy the exact strings. NEVER invent,
  paraphrase, translate, or merge names.
- Judge purely from the question's subject matter, not its wording or options.
- If no topic clearly fits, return topic null (and subtopic null). If the topic fits but no
  subtopic clearly does, return subtopic null. Do NOT force a match you are unsure of.
- Echo each question's Ref code EXACTLY in the "ref" field. Return one entry per question.

CHAPTER: {chapter}

TOPICS / SUBTOPICS IN THIS CHAPTER (the only allowed values):
{topic_tree}

QUESTIONS:
{questions}

Return ONLY valid JSON in exactly this structure:
{{
  "assignments": [
    {{"ref": "R1", "topic": "exact topic string or null", "subtopic": "exact subtopic string or null"}}
  ]
}}"""


RECOVERY_PROMPT = """ROLE: You file MCQs the first pass could not place into the exam's syllabus, so they are not
left unclassified.

TASK: For every question below, pick the single best CHAPTER (primary), then a topic and
subtopic within it when clear — all from the fixed tree.

HARD RULES (never violate):
- Choose chapter, topic and subtopic ONLY from the tree below — copy the exact strings. NEVER
  invent, paraphrase, translate, or merge names.
- `chapter` is the PRIMARY dimension: return the exact CHAPTER the chosen topic sits under. If
  a chapter clearly fits but no topic does, return that chapter with topic null.
- Judge purely from the question's subject matter. If the question genuinely fits no chapter
  (general/introductory/administrative), return chapter null — do NOT force a guess.
- Echo each question's Ref code EXACTLY in the "ref" field. Return one entry per question.

SYLLABUS TREE (the only allowed values):
{tree}

QUESTIONS:
{questions}

Return ONLY valid JSON in exactly this structure:
{{
  "assignments": [
    {{"ref": "R1", "chapter": "exact chapter string or null", "topic": "exact topic string or null", "subtopic": "exact subtopic string or null"}}
  ]
}}"""


def _format_questions(questions: list[dict]) -> str:
    """Ref-keyed, truncated question texts for the batch prompt."""
    lines: list[str] = []
    for i, q in enumerate(questions):
        text = (q.get("question_text") or "").strip()[:1200]
        lines.append(f"Ref: R{i + 1}\n{text}")
    return "\n\n".join(lines)


def align_assignments(questions: list[dict], assignments: object) -> dict[int, dict]:
    """Map the model's ref-keyed assignments back to question INDICES.

    Returns {question_index: assignment_dict}. Unknown/duplicate refs are ignored; a
    question with no matching ref is simply absent → the caller keeps it null. Never
    mis-assigns by position.
    """
    out: dict[int, dict] = {}
    if not isinstance(assignments, list):
        return out
    ref_to_index = {f"R{i + 1}": i for i in range(len(questions))}
    seen: set[str] = set()
    for item in assignments:
        if not isinstance(item, dict):
            continue
        raw = item.get("ref")
        ref = str(raw).strip().upper() if isinstance(raw, (str, int)) and str(raw).strip() else None
        if ref is None or ref in seen:
            continue
        idx = ref_to_index.get(ref)
        if idx is None:
            continue
        seen.add(ref)
        out[idx] = item
    return out


class MCQTopicAssignmentAgent:
    def __init__(self, db: AsyncSession, entity_id: uuid.UUID | None = None):
        self.db = db
        self.entity_id = entity_id
        self.provider = get_provider("thinking")

    async def assign_scoped(
        self, *, chapter: str, topic_tree: str, questions: list[dict],
    ) -> dict[int, dict]:
        """Assign topic/subtopic within a known chapter. Returns {index: {topic, subtopic}}."""
        prompt = SCOPED_PROMPT.format(
            chapter=chapter,
            topic_tree=(topic_tree or "(no topics configured for this chapter)")[:12000],
            questions=_format_questions(questions),
        )
        result = await self._call(prompt, "mcq_topic_assignment")
        return align_assignments(questions, result.get("assignments"))

    async def assign_recovery(
        self, *, tree_text: str, questions: list[dict],
    ) -> dict[int, dict]:
        """Assign chapter + topic/subtopic from the full tree. Returns
        {index: {chapter, topic, subtopic}}."""
        prompt = RECOVERY_PROMPT.format(
            tree=(tree_text or "(no syllabus configured)")[:16000],
            questions=_format_questions(questions),
        )
        result = await self._call(prompt, "mcq_topic_recovery")
        return align_assignments(questions, result.get("assignments"))

    async def _call(self, prompt: str, task_type: str) -> dict:
        audit_ctx = {
            "db": self.db,
            "agent_type": "MCQTopicAssignmentAgent",
            "task_type": task_type,
            "entity_type": "mcq_document",
            "entity_id": self.entity_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"MCQ topic assignment failed: {exc}") from exc
        if not isinstance(result, dict):
            raise AIResponseError("MCQ topic assignment did not return an object")
        return result
