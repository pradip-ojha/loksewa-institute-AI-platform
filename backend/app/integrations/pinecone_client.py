import logging
from functools import lru_cache

from pinecone import Pinecone

from app.core.config import settings

logger = logging.getLogger(__name__)


class PineconeClient:
    def __init__(self) -> None:
        pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        self._index = pc.Index(host=settings.PINECONE_INDEX_HOST)

    def upsert_vectors(self, vectors: list[dict]) -> None:
        if not vectors:
            return
        # Pinecone accepts batches of up to 100 vectors
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            self._index.upsert(vectors=vectors[i : i + batch_size])

    def query(
        self,
        embedding: list[float],
        top_k: int = 10,
        filter_dict: dict | None = None,
    ) -> list[dict]:
        result = self._index.query(
            vector=embedding,
            top_k=top_k,
            filter=filter_dict,
            include_metadata=True,
        )
        return [
            {"id": m.id, "score": m.score, "metadata": m.metadata}
            for m in result.matches
        ]

    def delete_vectors(self, ids: list[str]) -> None:
        if not ids:
            return
        self._index.delete(ids=ids)


@lru_cache
def get_pinecone() -> PineconeClient:
    return PineconeClient()
