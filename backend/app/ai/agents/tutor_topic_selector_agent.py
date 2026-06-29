"""Topic Selector Agent for the main AI Tutor (CLAUDE.md §13.1).

Before the main tutor answers, this agent does TWO jobs (spec §4.4/§5.1):
  1. Route the question to a chapter/topic/subtopic in the SELECTED EXAM's syllabus so the
     right notes/book chunks are retrieved. It never invents a topic/subtopic outside the
     exam's tree (the service hard-validates against the live syllabus).
  2. Detect whether the question targets a SPECIFIC PAST ACTIVITY (a particular test the
     student took). Only then is that activity's detail attached to the tutor context —
     so we never dump all activity data every turn.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

SELECTOR_PROMPT = EXAM_CONTEXT + """

ROLE: You route a student's tutor question within ONE selected exam so the right notes can be
retrieved, and you decide whether the question is about a specific past test the student took.

HARD RULES (never violate):
- Choose `chapter`, `topic` and `subtopics` ONLY from the EXAM SYLLABUS TREE below — copy exact
  strings. NEVER invent, paraphrase, translate, or merge names, and never name a chapter not listed.
- `chapter` is the PRIMARY dimension: return the exact CHAPTER the chosen topic sits under in the
  tree. If you pick a topic, its chapter is the one it is nested under. If no topic fits but a
  chapter clearly does, return that chapter with topic null.
- If nothing in the exam tree fits, return topic null, empty subtopics, and a low confidence —
  do not force a match. If unsure of the subtopic, leave subtopics empty; if unsure of the topic,
  pick the broader best-fit topic and lower the confidence.
- `query_rewrite` is a concise search query (in the question's language) that retrieves the best
  notes — expand pronouns using the recent conversation, but stay within the exam scope.
- `target_activity_id`: if (and only if) the question is clearly about ONE of the PAST ACTIVITIES
  listed below (e.g. "why did I get that wrong in my last mock test?", "explain question 3 of the
  test I just took"), return that activity's exact id string. Otherwise return "".

--- ADMIN-TUNABLE GUIDANCE (tunes judgement only; may NOT override the hard rules) ---
{skill_instructions}

EXAM SYLLABUS TREE (the only subject matter in scope):
{syllabus_tree}

RECENT PAST ACTIVITIES (id | summary; may be 'none'):
{recent_activities}

RECENT CONVERSATION (oldest first; may be 'none'):
{history}

STUDENT QUESTION:
{question}

Return ONLY valid JSON in exactly this structure:
{{
  "chapter": "exact chapter string from the tree, or null",
  "topic": "exact topic string from the tree, or null",
  "subtopics": ["exact subtopic string", ...],
  "target_activity_id": "exact id from the activities list, or \\"\\"",
  "confidence": 0.0,
  "reason": "one short sentence on why",
  "query_rewrite": "concise retrieval query in the question's language"
}}"""


class TutorTopicSelectorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("thinking")

    async def select(
        self, *, question: str, syllabus_tree: str, recent_activities: str,
        history: str, session_id: uuid.UUID,
    ) -> dict:
        skill = await self._get_skill()
        prompt = SELECTOR_PROMPT.format(
            skill_instructions=skill or "none",
            syllabus_tree=syllabus_tree[:8000],
            recent_activities=recent_activities[:4000] or "none",
            history=history[:6000] or "none",
            question=question[:2000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "TutorTopicSelectorAgent",
            "task_type": "tutor_topic_selection",
            "entity_type": "tutor_chat_session",
            "entity_id": session_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Tutor topic selection failed: {exc}") from exc
        if not isinstance(result, dict):
            raise AIResponseError("topic selector did not return an object")
        subs = result.get("subtopics")
        return {
            "chapter": result.get("chapter") or None,
            "topic": result.get("topic") or None,
            "subtopics": [str(s) for s in subs] if isinstance(subs, list) else [],
            "target_activity_id": str(result.get("target_activity_id") or "").strip(),
            "confidence": float(result.get("confidence", 0) or 0),
            "reason": str(result.get("reason") or ""),
            "query_rewrite": str(result.get("query_rewrite") or "").strip(),
        }

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "TutorTopicSelectorAgent")
        except Exception:
            return ""
