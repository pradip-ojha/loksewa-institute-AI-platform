"""Skill layer service — stub until Phase 10 builds the full implementation."""

from sqlalchemy.ext.asyncio import AsyncSession


async def get_active_skill_text(db: AsyncSession, agent_type: str, scope_type: str = "global", scope_id: str | None = None) -> str:
    """Return active skill instruction text for an agent. Returns empty string if no skill configured."""
    try:
        from app.modules.skill_layer.models import AgentSkillVersion
        from sqlalchemy import select

        q = (
            select(AgentSkillVersion)
            .where(
                AgentSkillVersion.agent_type == agent_type,
                AgentSkillVersion.scope_type == scope_type,
                AgentSkillVersion.status == "active",
            )
        )
        if scope_id:
            q = q.where(AgentSkillVersion.scope_id == scope_id)
        else:
            q = q.where(AgentSkillVersion.scope_id.is_(None))

        result = await db.execute(q.order_by(AgentSkillVersion.version_number.desc()).limit(1))
        skill = result.scalar_one_or_none()
        return skill.instruction_text if skill else ""
    except Exception:
        return ""
