"""Backfill embeddings for chunks that don't have them yet.

Run from project root:
    python -m scripts.backfill_embeddings

Requires:
    - KIB_DATABASE_URL env var (or defaults to localhost)
    - FIREWORKS_API_KEY env var
"""

import json
import math
import os
import sys
import urllib.request
from typing import List

import psycopg

DB_URL = os.environ.get(
    "KIB_DATABASE_URL",
    "postgresql://localhost/kib",
)
FIREWORKS_API_KEY = os.environ.get("FIREWORKS_API_KEY", "fw_JQzU8TxGETYnmNxpMDDyAE")
FIREWORKS_EMBED_URL = "https://api.fireworks.ai/inference/v1/embeddings"
EMBEDDING_MODEL = os.environ.get("KIB_EMBEDDING_MODEL", "accounts/fireworks/models/qwen3-embedding-8b")
EMBEDDING_DIM = int(os.environ.get("KIB_EMBEDDING_DIM", "768"))
BATCH_SIZE = 32


def _truncate_normalize(vec: List[float], dim: int) -> List[float]:
    truncated = vec[:dim]
    norm = math.sqrt(sum(x * x for x in truncated))
    return [x / norm for x in truncated] if norm > 0 else truncated


def _embed(texts: List[str]) -> List[List[float]]:
    payload = json.dumps({
        "model": EMBEDDING_MODEL,
        "input": texts,
        "dimensions": EMBEDDING_DIM,
    }).encode()
    req = urllib.request.Request(
        FIREWORKS_EMBED_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {FIREWORKS_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    sorted_data = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in sorted_data]


def main() -> int:
    print(f"[BACKFILL] Connecting to DB...")
    conn = psycopg.connect(DB_URL)
    cur = conn.cursor()

    # Find chunks without embeddings
    cur.execute("""
        SELECT c.id, c.text
        FROM chunks c
        LEFT JOIN embeddings e ON e.chunk_id = c.id
        WHERE e.chunk_id IS NULL
        ORDER BY c.id
    """)
    rows = cur.fetchall()

    if not rows:
        print("[BACKFILL] All chunks already have embeddings. Nothing to do.")
        conn.close()
        return 0

    print(f"[BACKFILL] Found {len(rows)} chunks without embeddings.")
    print(f"[BACKFILL] Using Fireworks model: {EMBEDDING_MODEL} ({EMBEDDING_DIM} dims)")

    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        chunk_ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]

        embeddings = _embed(texts)

        for chunk_id, embedding in zip(chunk_ids, embeddings):
            cur.execute(
                "INSERT INTO embeddings (chunk_id, embedding, model) VALUES (%s, %s, %s)",
                (chunk_id, embedding, EMBEDDING_MODEL),
            )

        conn.commit()
        total += len(batch)
        print(f"  [{total}/{len(rows)}] embedded")

    conn.close()
    print(f"[BACKFILL] Done. {total} embeddings created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
