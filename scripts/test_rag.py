#!/usr/bin/env python3
"""Quick end-to-end test of the RAG pipeline against the local DB."""

import sys, math, json
import httpx
import psycopg
from psycopg.rows import dict_row
from pgvector.psycopg import register_vector

DB_URL = "postgresql://localhost/kib"
FIREWORKS_KEY = "fw_JQzU8TxGETYnmNxpMDDyAE"
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/embeddings"
MODEL = "accounts/fireworks/models/qwen3-embedding-8b"
DIM = 768


def embed(text: str) -> list[float]:
    resp = httpx.post(
        FIREWORKS_URL,
        json={"model": MODEL, "input": [text], "dimensions": DIM},
        headers={"Authorization": f"Bearer {FIREWORKS_KEY}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def test_query(conn, question: str, role: str = "front_desk", top_k: int = 5):
    print(f"\n{'='*60}")
    print(f"Q: {question}")
    print(f"Role: {role} | Top-K: {top_k}")
    print("="*60)

    # 1. Embed the query
    qvec = embed(question)
    print(f"[OK] Query embedded ({len(qvec)} dims)")

    # 2. Get accessible doc IDs
    doc_ids = [
        str(r["id"]) for r in conn.execute(
            """SELECT DISTINCT d.id FROM documents d
               JOIN document_acl a ON a.document_id = d.id
               JOIN roles r ON r.id = a.role_id
               WHERE d.status = 'approved' AND r.name = %s""",
            (role,),
        ).fetchall()
    ]
    print(f"[OK] {len(doc_ids)} accessible documents for role '{role}'")

    # 3. Vector search
    vec_str = "[" + ",".join(str(x) for x in qvec) + "]"
    rows = conn.execute(
        """SELECT
               c.text,
               c.page_start,
               d.title,
               dv.source_uri,
               (e.embedding <=> %s::vector) AS distance
           FROM embeddings e
           JOIN chunks c ON c.id = e.chunk_id
           JOIN document_versions dv ON dv.id = c.document_version_id
           JOIN documents d ON d.id = dv.document_id
           WHERE d.id = ANY(%s)
             AND d.status = 'approved'
             AND dv.is_active = true
             AND e.model = %s
           ORDER BY e.embedding <=> %s::vector
           LIMIT %s""",
        (vec_str, doc_ids, MODEL, vec_str, top_k),
    ).fetchall()

    print(f"[OK] Retrieved {len(rows)} chunks\n")
    for i, row in enumerate(rows, 1):
        dist = row["distance"]
        title = (row["title"] or "")[:60]
        uri = (row["source_uri"] or "")[:80]
        text_preview = row["text"][:120].replace("\n", " ")
        page = row["page_start"]
        print(f"  #{i} (dist={dist:.4f}) [{title}] p.{page}")
        print(f"     {uri}")
        print(f"     \"{text_preview}...\"")
        print()


if __name__ == "__main__":
    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        register_vector(conn)
        # English query about KIB
        test_query(conn, "What are KIB's terms and conditions for online banking?")

        # Arabic query about CBK
        test_query(conn, "ما هي سياسة بنك الكويت المركزي بشأن السيولة؟")

        # English query about CBK regulations
        test_query(conn, "What are the capital adequacy requirements set by CBK?")

    print("\n✅ RAG pipeline end-to-end test complete!")
