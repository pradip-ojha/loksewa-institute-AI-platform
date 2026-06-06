"""Skill Builder agent (CLAUDE.md §14).

Helps the Institute Admin refine an AI agent's behavior through chat. Given the
target agent's current active instruction and the conversation so far, it replies
conversationally and proposes a full revised instruction text. The admin reviews
the proposed instruction, then approves it to create a new active skill version.

Produces `instruction_text` only this stage; structured_rules_json is left null.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.model_router import get_provider
from app.core.exceptions import AIResponseError

logger = logging.getLogger(__name__)

BUILDER_PROMPT = """You are the Skill Builder for an AI-powered exam preparation platform. The Institute Admin is improving the behavior of ONE backend AI agent by chatting with you. Your job is to understand the admin's intent and rewrite that agent's instruction text accordingly.

TARGET AGENT: {agent_type}

THIS AGENT'S CURRENT ACTIVE INSTRUCTION:
{current_instruction}

GUIDANCE FOR HOW YOU SHOULD WORK:
{skill_instructions}

CONVERSATION SO FAR:
{conversation}

ADMIN'S LATEST MESSAGE:
{latest_message}

RULES:
1. The proposed instruction must be the COMPLETE, standalone replacement instruction for the agent — not a diff and not just the change. Preserve the agent's existing correct behavior and only adjust what the admin asked for.
2. Keep it concise, clear, and directly actionable for an AI agent. Do not add meta-commentary inside the instruction.
3. Never weaken hard safety/quality rules already present (e.g. mark caps, "never reference the source document", language preservation) unless the admin explicitly asks.
4. If the admin is only asking a question or hasn't requested a concrete change yet, reply helpfully and return an EMPTY proposed_instruction.
5. Match the admin's language in your reply.

Return ONLY valid JSON with exactly these keys:
{{"reply": "your conversational reply to the admin", "proposed_instruction": "the full revised instruction text, or empty string if no change yet", "change_summary": "one short sentence describing the change, or empty string"}}"""


class SkillBuilderAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.provider = get_provider("reasoning")

    async def _get_skill(self) -> str:
        try:
            from app.modules.skill_layer.service import get_active_skill_text
            return await get_active_skill_text(self.db, "SkillBuilderAgent")
        except Exception:
            return ""

    async def refine(
        self,
        *,
        agent_type: str,
        current_instruction: str,
        conversation: list[dict],
        latest_message: str,
    ) -> dict:
        """Return {reply, proposed_instruction, change_summary}.

        `conversation` is a list of {role, content} (prior turns, excluding the
        latest admin message).
        """
        convo_text = "\n".join(
            f"{m.get('role', 'admin').upper()}: {m.get('content', '')}" for m in conversation
        ) or "(no prior messages)"

        prompt = BUILDER_PROMPT.format(
            agent_type=agent_type,
            current_instruction=current_instruction or "(none configured)",
            skill_instructions=await self._get_skill()
            or "Refine the agent's instruction precisely and conservatively.",
            conversation=convo_text[:12000],
            latest_message=latest_message[:4000],
        )
        audit_ctx = {
            "db": self.db,
            "agent_type": "SkillBuilderAgent",
            "task_type": "skill_builder_chat",
            "entity_type": "agent_skill",
            "entity_id": None,
        }
        try:
            result = await self.provider.generate_text(prompt, schema={}, audit_ctx=audit_ctx)
        except Exception as exc:
            raise RuntimeError(f"Skill builder chat failed: {exc}") from exc

        if not isinstance(result, dict) or "reply" not in result:
            raise AIResponseError("skill builder did not return a valid reply")

        return {
            "reply": (result.get("reply") or "").strip(),
            "proposed_instruction": (result.get("proposed_instruction") or "").strip(),
            "change_summary": (result.get("change_summary") or "").strip(),
        }
