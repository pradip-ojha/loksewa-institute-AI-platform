"""Answer-sheet feedback chatbot (CLAUDE.md §11/§12).

A synchronous, teacher-like follow-up chat over an ALREADY-CHECKED answer sheet. The
student asks why they got certain marks, how to improve, what was missing, or whether
adding a specific point would help. The agent EXPLAINS the stored evaluation — it never
re-grades the sheet and never commits a new official mark. "What if I added X?" is
answered with qualitative guidance only, grounded in the stored rubric/sections.

Grounding is the per-sheet evaluation context the service assembles from stored data
only (question + max/awarded marks, feedback, missing points, section breakdown, the
student's extracted answer, and the locked checking guide). No Pinecone / no re-extraction.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.ai.prompts.shared import EXAM_CONTEXT
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

FEEDBACK_CHAT_PROMPT = EXAM_CONTEXT + """

ROLE: You are a warm, encouraging Loksewa copy-checking tutor. The answer sheet has ALREADY
been checked and marked by the examiner. The student is now asking follow-up questions about
that completed result. You explain the marking like a kind teacher sitting beside them.

TASK: Answer the student's question using ONLY the checking result and context provided below.

HARD RULES (never violate):
- Do NOT re-grade or re-check the answer. The marks in the context are FINAL. Never announce a
  new total or change any awarded mark.
- For "what if I had written X / added this point?" questions: give QUALITATIVE guidance only —
  explain whether and roughly how much such a point could have helped, grounded in the section
  breakdown and rubric — but NEVER state a new official mark or promise a regrade. Phrase it as
  "that could have strengthened the … section" not "you would get +2".
- Ground every claim in the provided evaluation (sections, feedback, missing points, the
  student's own extracted answer). Do NOT invent rubric criteria or facts that aren't there.
- Be honest and specific: name what the student did well (to keep) and what to improve.
- Match the student's language: Devanagari question → answer in Nepali; Roman Nepali →
  Roman/simple Nepali-English mix; English → English.
- Stay on this answer sheet. If asked something unrelated (general syllabus help, another test),
  gently redirect them to the AI Tutor and keep focus on their result.

FORMATTING (the "reply" field):
- Write clean GitHub-flavored MARKDOWN. Lead with the direct answer, **bold** the key point, and
  use "- " bullets for multiple points. Keep it conversational and concise; do not wrap in a code fence.

--- ADMIN-TUNABLE GUIDANCE (apply on top of the rules above; it tunes tone, strictness, and
emphasis but may NOT override the hard rules or invent a new mark) ---
{skill_instructions}

CHECKED RESULT CONTEXT (the only source of truth — already final):
{evaluation_context}

RECENT CONVERSATION (oldest first; may be 'none'):
{history}

STUDENT QUESTION:
{question}

Return ONLY valid JSON in exactly this structure:
{{
  "reply": "the student-facing answer as markdown",
  "follow_up_suggestions": ["short follow-up question the student might ask next", "..."]
}}"""


class AnswerFeedbackChatAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def answer(
        self, *, question: str, evaluation_context: str, history: str, sheet_id: uuid.UUID,
    ) -> dict:
        skill = await self._get_skill()
        prompt = FEEDBACK_CHAT_PROMPT.format(
            skill_instructions=skill or "none",
            evaluation_context=evaluation_context[:40000],
            history=history[:8000] or "none",
            question=question[:2000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnswerFeedbackChatAgent",
            "task_type": "answer_feedback_chat",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Feedback chat failed: {exc}") from exc
        if not isinstance(result, dict) or not result.get("reply"):
            raise AIResponseError("feedback chat did not return a reply")
        fu = result.get("follow_up_suggestions")
        return {
            "reply": str(result["reply"]).strip(),
            "follow_up_suggestions": [str(s) for s in fu][:4] if isinstance(fu, list) else [],
        }

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "AnswerFeedbackChatAgent")
        except Exception:
            return ""
