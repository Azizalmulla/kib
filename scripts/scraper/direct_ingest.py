"""Direct database ingestion — writes straight to local PostgreSQL."""

import hashlib
import json
import math
import os
import time
from typing import List, Optional
from uuid import uuid4

import httpx

import psycopg
from psycopg.types.json import Json

DB_URL = os.environ.get(
    "KIB_DATABASE_URL",
    "postgresql://localhost/kib",
)
FIREWORKS_API_KEY = os.environ.get("FIREWORKS_API_KEY", "")
FIREWORKS_EMBED_URL = "https://api.fireworks.ai/inference/v1/embeddings"
EMBEDDING_MODEL = os.environ.get("KIB_EMBEDDING_MODEL", "accounts/fireworks/models/qwen3-embedding-8b")
EMBEDDING_DIM = int(os.environ.get("KIB_EMBEDDING_DIM", "768"))
CHUNK_SIZE = int(os.environ.get("KIB_CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.environ.get("KIB_CHUNK_OVERLAP", "100"))


def _chunk_text(text: str) -> List[dict]:
    text = text.replace("\x00", "")
    chunks = []
    start = 0
    index = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        chunk = text[start:end]
        chunks.append({
            "chunk_index": index,
            "text": chunk,
            "offset_start": start,
            "offset_end": end,
            "hash": hashlib.sha256(chunk.encode()).hexdigest(),
            "page_start": 1,
            "page_end": 1,
        })
        index += 1
        start = end - CHUNK_OVERLAP if end - CHUNK_OVERLAP > start else end
    return chunks


def _truncate_normalize(vec: List[float], dim: int) -> List[float]:
    truncated = vec[:dim]
    norm = math.sqrt(sum(x * x for x in truncated))
    return [x / norm for x in truncated] if norm > 0 else truncated


def _embed(texts: List[str], retries: int = 5) -> List[List[float]]:
    if not texts:
        return []
    payload = {
        "model": EMBEDDING_MODEL,
        "input": texts,
        "dimensions": EMBEDDING_DIM,
    }
    headers = {
        "Authorization": f"Bearer {FIREWORKS_API_KEY}",
    }
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(FIREWORKS_EMBED_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in sorted_data]
        except Exception as e:
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"    [EMBED-RETRY] attempt {attempt+1} failed: {e}, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise


def ingest_page(
    text: str,
    title: str,
    source_uri: str,
    language: str,
    doc_type: str = "web_page",
    access_tags: Optional[dict] = None,
    allowed_roles: str = "front_desk,compliance",
) -> Optional[dict]:
    sha256 = hashlib.sha256(text.encode()).hexdigest()
    chunks = _chunk_text(text)
    if not chunks:
        return None

    embeddings = _embed([c["text"] for c in chunks])
    role_names = [r.strip() for r in allowed_roles.split(",") if r.strip()]
    tags = access_tags or {}

    with psycopg.connect(DB_URL, row_factory=psycopg.rows.dict_row) as conn:
        # Ensure roles
        role_ids = []
        for rn in role_names:
            row = conn.execute(
                "INSERT INTO roles (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (rn,),
            ).fetchone()
            role_ids.append(row["id"])

        # Create document
        doc_row = conn.execute(
            "INSERT INTO documents (title, doc_type, language, status, access_tags) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (title, doc_type, language, "approved", Json(tags)),
        ).fetchone()
        doc_id = doc_row["id"]

        # Create version
        ver_row = conn.execute(
            "INSERT INTO document_versions (document_id, version, source_uri, sha256, page_count) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (doc_id, "v1", source_uri, sha256, 1),
        ).fetchone()
        ver_id = ver_row["id"]

        # ACL
        for rid in role_ids:
            conn.execute(
                "INSERT INTO document_acl (document_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (doc_id, rid),
            )

        # Chunks + embeddings
        for idx, chunk in enumerate(chunks):
            chunk_row = conn.execute(
                """INSERT INTO chunks (document_version_id, chunk_index, text, page_start, page_end, offset_start, offset_end, hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (ver_id, chunk["chunk_index"], chunk["text"], chunk["page_start"], chunk["page_end"],
                 chunk["offset_start"], chunk["offset_end"], chunk["hash"]),
            ).fetchone()

            conn.execute(
                "INSERT INTO embeddings (chunk_id, embedding, model) VALUES (%s, %s, %s)",
                (chunk_row["id"], embeddings[idx], EMBEDDING_MODEL),
            )

        conn.commit()

    return {"document_id": str(doc_id), "chunks_ingested": len(chunks)}


def _chunk_pages(pages: list) -> List[dict]:
    """Chunk a multi-page PDF document. Each page's text is chunked separately,
    preserving page numbers for citations."""
    all_chunks = []
    index = 0
    for p in pages:
        text = p["text"].replace("\x00", "")
        page_num = p["page"]
        start = 0
        while start < len(text):
            end = min(len(text), start + CHUNK_SIZE)
            chunk = text[start:end]
            all_chunks.append({
                "chunk_index": index,
                "text": chunk,
                "offset_start": start,
                "offset_end": end,
                "hash": hashlib.sha256(chunk.encode()).hexdigest(),
                "page_start": page_num,
                "page_end": page_num,
            })
            index += 1
            start = end - CHUNK_OVERLAP if end - CHUNK_OVERLAP > start else end
    return all_chunks


def ingest_pdf(
    pages: list,
    title: str,
    source_uri: str,
    language: str,
    doc_type: str = "pdf",
    access_tags: Optional[dict] = None,
    allowed_roles: str = "front_desk,compliance",
) -> Optional[dict]:
    """Ingest a parsed PDF (list of {page, text} dicts) into the local DB."""
    full_text = "\n".join(p["text"].replace("\x00", "") for p in pages)
    sha256 = hashlib.sha256(full_text.encode()).hexdigest()
    chunks = _chunk_pages(pages)
    if not chunks:
        return None

    # Embed in batches of 32
    all_embeddings = []
    for b in range(0, len(chunks), 32):
        batch_texts = [c["text"] for c in chunks[b : b + 32]]
        all_embeddings.extend(_embed(batch_texts))

    role_names = [r.strip() for r in allowed_roles.split(",") if r.strip()]
    tags = access_tags or {}

    with psycopg.connect(DB_URL, row_factory=psycopg.rows.dict_row) as conn:
        role_ids = []
        for rn in role_names:
            row = conn.execute(
                "INSERT INTO roles (name) VALUES (%s) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (rn,),
            ).fetchone()
            role_ids.append(row["id"])

        doc_row = conn.execute(
            "INSERT INTO documents (title, doc_type, language, status, access_tags) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (title, doc_type, language, "approved", Json(tags)),
        ).fetchone()
        doc_id = doc_row["id"]

        ver_row = conn.execute(
            "INSERT INTO document_versions (document_id, version, source_uri, sha256, page_count) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (doc_id, "v1", source_uri, sha256, len(pages)),
        ).fetchone()
        ver_id = ver_row["id"]

        for rid in role_ids:
            conn.execute(
                "INSERT INTO document_acl (document_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (doc_id, rid),
            )

        for idx, chunk in enumerate(chunks):
            chunk_row = conn.execute(
                """INSERT INTO chunks (document_version_id, chunk_index, text, page_start, page_end, offset_start, offset_end, hash)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (ver_id, chunk["chunk_index"], chunk["text"], chunk["page_start"], chunk["page_end"],
                 chunk["offset_start"], chunk["offset_end"], chunk["hash"]),
            ).fetchone()

            conn.execute(
                "INSERT INTO embeddings (chunk_id, embedding, model) VALUES (%s, %s, %s)",
                (chunk_row["id"], all_embeddings[idx], EMBEDDING_MODEL),
            )

        conn.commit()

    return {"document_id": str(doc_id), "chunks_ingested": len(chunks), "pages": len(pages)}
