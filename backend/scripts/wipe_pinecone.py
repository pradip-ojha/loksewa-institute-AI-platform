"""Wipe every vector from the Pinecone index.

Use after an architecture / chunk-metadata change: the old vectors carry stale metadata
(pre-`exam_id` routing keys) that would poison retrieval, and the DB `knowledge_chunks`
table is the source of truth for what should exist. With that table empty, the index must
be empty too — this brings them back in sync so fresh knowledge can be re-uploaded clean.

Run from the backend/ directory:
    python -m scripts.wipe_pinecone
"""
from app.integrations.pinecone_client import get_pinecone


def main() -> None:
    pc = get_pinecone()
    before = pc.stats()
    print(f"Before: {before.get('total_vector_count', '?')} vectors "
          f"across namespaces {list((before.get('namespaces') or {}).keys()) or ['(default)']}")

    pc.delete_all()
    print("Issued delete_all on the default namespace.")

    after = pc.stats()
    print(f"After:  {after.get('total_vector_count', '?')} vectors")


if __name__ == "__main__":
    main()
