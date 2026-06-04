import logging
from functools import lru_cache

from pinecone import Pinecone

from app.core.config import settings
from app.core.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)


class PineconeClient:
    def __init__(self) -> None:
        self._pc = Pinecone(api_key=settings.PINECONE_API_KEY)
        self._index = None

    def _get_index(self):
        """Build the index handle lazily and rebuild it if a prior handle went
        stale (e.g. transient network error left a broken connection cached)."""
        if self._index is None:
            self._index = self._pc.Index(host=settings.PINECONE_INDEX_HOST)
        return self._index

    def _reset_index(self) -> None:
        self._index = None

    def upsert_vectors(self, vectors: list[dict]) -> None:
        if not vectors:
            return
        # Pinecone accepts batches of up to 100 vectors
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i : i + batch_size]
            try:
                self._get_index().upsert(vectors=batch)
            except Exception as exc:
                self._reset_index()
                raise ExternalServiceError("pinecone", f"upsert failed: {exc}") from exc

    def query(
        self,
        embedding: list[float],
        top_k: int = 10,
        filter_dict: dict | None = None,
    ) -> list[dict]:
        try:
            result = self._get_index().query(
                vector=embedding,
                top_k=top_k,
                filter=filter_dict,
                include_metadata=True,
            )
        except Exception as exc:
            self._reset_index()
            raise ExternalServiceError("pinecone", f"query failed: {exc}") from exc
        return [
            {"id": m.id, "score": m.score, "metadata": dict(m.metadata) if m.metadata else {}}
            for m in (result.matches or [])
        ]

    def delete_vectors(self, ids: list[str]) -> None:
        if not ids:
            return
        try:
            self._get_index().delete(ids=ids)
        except Exception as exc:
            self._reset_index()
            raise ExternalServiceError("pinecone", f"delete failed: {exc}") from exc


@lru_cache
def get_pinecone() -> PineconeClient:
    return PineconeClient()
