"""Feedback-chat question selector (CLAUDE.md §12.1).

A cheap router that runs BEFORE the main AnswerFeedbackChatAgent. Given the student's
follow-up question (+ recent history) and a compact index of the sheet's questions, it
decides WHICH question(s) full grading context the chat actually needs — so we don't pour
every question's evaluation + checking guide into the prompt on every turn (cost control).

It only SELECTS; it never grades or explains. When the question is broad ("overall how did
I do?", "what should I focus on?") it returns needs_all=true and the full context is used.
Runs on the fast gpt-5-mini tier — this is a routing decision, not reasoning.
"""
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider

logger = logging.getLogger(__name__)

SELECTOR_PROMPT = """You route a student's follow-up question about their ALREADY-CHECKED answer
sheet to the specific exam question(s) it is about, so only the relevant grading context is loaded.

You do NOT answer or grade. You only pick which question numbers the question refers to.

RULES:
- Return the exact question_number string(s) the student's message is about.
- If the message is about overall performance, the whole sheet, general advice, or you are unsure
  which question it targets, set "needs_all": true (and you may leave "question_numbers" empty).
- Use the recent conversation to resolve references like "that one" / "the next question".
- Never invent a question number that is not in the list.

QUESTIONS ON THIS SHEET (number — short text — awarded/max):
{question_index}

RECENT CONVERSATION (oldest first; may be 'none'):
{history}

STUDENT MESSAGE:
{question}

Return ONLY valid JSON: {{"question_numbers": ["1", ...], "needs_all": false}}"""


class AnswerFeedbackSelectorAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("routing")  # gpt-5-mini (fast) — cheap routing

    async def select(
        self, *, question: str, question_index: str, history: str, sheet_id: uuid.UUID,
    ) -> dict:
        """Returns {"question_numbers": [str], "needs_all": bool}. Fail-open: on any
        error returns needs_all=True so the chat still gets complete context."""
        prompt = SELECTOR_PROMPT.format(
            question_index=question_index or "none",
            history=history[:4000] or "none",
            question=question[:2000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "AnswerFeedbackSelectorAgent",
            "task_type": "feedback_question_select",
            "entity_type": "student_answer_sheet",
            "entity_id": sheet_id,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:  # noqa: BLE001 — fail open to full context
            logger.warning("Feedback question selector failed (using all context): %s", exc)
            return {"question_numbers": [], "needs_all": True}
        if not isinstance(result, dict):
            return {"question_numbers": [], "needs_all": True}
        nums = result.get("question_numbers")
        nums = [str(n) for n in nums] if isinstance(nums, list) else []
        needs_all = bool(result.get("needs_all")) or not nums
        return {"question_numbers": nums, "needs_all": needs_all}
