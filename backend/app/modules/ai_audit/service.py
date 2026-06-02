import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai_audit.models import AIRequest, AIOutput

logger = logging.getLogger(__name__)


async def log_ai_request(
    db: AsyncSession,
    *,
    provider: str,
    model: str,
    api_version: str | None = None,
    agent_type: str | None = None,
    task_type: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    status: str = "success",
    latency_ms: int | None = None,
    error_message: str | None = None,
    related_entity_type: str | None = None,
    related_entity_id: uuid.UUID | None = None,
    output_summary: str | None = None,
) -> AIRequest:
    req = AIRequest(
        provider=provider,
        model=model,
        api_version=api_version,
        agent_type=agent_type,
        task_type=task_type,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        status=status,
        latency_ms=latency_ms,
        error_message=error_message,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
    )
    db.add(req)
    await db.flush()

    if output_summary:
        db.add(AIOutput(request_id=req.id, output_summary=output_summary))

    try:
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to commit AI audit record: %s", exc)
        await db.rollback()

    return req
