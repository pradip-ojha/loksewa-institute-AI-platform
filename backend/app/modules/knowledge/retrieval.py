"""Shared knowledge retrieval — dual (prose + model_qa) Pinecone query.

Knowledge chunks come in two shapes (CLAUDE.md §8):
  • prose  — book / notes / handout / reference_material, embedded on their content.
  • model_qa — one question↔answer pair per chunk (content = answer), embedded on
    question+answer and carrying a "question" in metadata.

A single vector query would let the two content shapes compete for the same top_k slots. Instead we
run TWO parallel queries over the same base filter — one restricted to model_qa, one excluding it —
and merge, so every fetch draws from both sources. Callers (video Q&A / main tutor / subjective
skill-gen) format the merged hits, surfacing model_qa pairs distinctly ("the model answer to exactly
this question").
"""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.pinecone_client import get_pinecone

logger = logging.getLogger(__name__)

_MODEL_QA = "model_qa"


@dataclass
class KnowledgeHit:
    """One retrieved knowledge chunk, in merged (prose-then-qa, each score-ordered) order."""
    content: str
    topic: str | None
    subtopic: str | None
    chunk_id: str
    is_qa: bool
    question: str | None  # only set for model_qa hits (from Pinecone metadata)


async def query_knowledge_dual(
    db: AsyncSession,
    *,
    embedding: list[float],
    base_filter: dict,
    prose_top_k: int,
    qa_top_k: int = 3,
) -> list[KnowledgeHit]:
    """Run two parallel Pinecone queries over the SAME `base_filter` (exam_id [+ chapter/topic/
    subtopic]) — one for prose content, one for model_qa pairs — and merge the results.

    Returns hits in prose-then-qa order, each set in Pinecone score order. Best-effort: returns
    [] on any failure so callers keep working grounded in their own primary source.
    """
    from app.modules.knowledge.models import KnowledgeChunk

    try:
        prose_filter = {**base_filter, "document_type": {"$nin": [_MODEL_QA]}}
        qa_filter = {**base_filter, "document_type": _MODEL_QA}

        # The two content shapes are disjoint by document_type, so no vector is returned twice.
        # Offload the blocking (sync) Pinecone SDK calls so they can't stall the event loop.
        prose_matches, qa_matches = await asyncio.gather(
            asyncio.to_thread(get_pinecone().query, embedding, prose_top_k, prose_filter),
            asyncio.to_thread(get_pinecone().query, embedding, qa_top_k, qa_filter),
        )

        ordered = (
            [(m, False) for m in (prose_matches or [])]
            + [(m, True) for m in (qa_matches or [])]
        )
        vector_ids = [m["id"] for m, _ in ordered if m.get("id")]
        if not vector_ids:
            return []

        r = await db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.pinecone_vector_id.in_(vector_ids))
        )
        chunks = {c.pinecone_vector_id: c for c in r.scalars().all()}

        hits: list[KnowledgeHit] = []
        for m, is_qa in ordered:
            c = chunks.get(m.get("id"))
            if not c:
                continue
            question = (m.get("metadata") or {}).get("question") if is_qa else None
            hits.append(KnowledgeHit(
                content=c.content,
                topic=c.topic,
                subtopic=c.subtopic,
                chunk_id=str(c.id),
                is_qa=is_qa,
                question=question,
            ))
        return hits
    except Exception as exc:
        logger.warning("dual knowledge retrieval failed (continuing without it): %s", exc)
        return []
