"""Direct database ingestion — bypasses FastAPI, writes straight to Render DB."""

import hashlib
import json
import os
from typing import List, Optional
from uuid import uuid4

import psycopg
from psycopg.types.json import Json
from sentence_transformers import SentenceTransformer

DB_URL = os.environ.get(
    "KIB_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/kib",
)
EMBEDDING_MODEL = os.environ.get("KIB_EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
CHUNK_SIZE = int(os.environ.get("KIB_CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.environ.get("KIB_CHUNK_OVERLAP", "100"))

_model: Optional[SentenceTransformer] = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        print(f"[MODEL] Loading {EMBEDDING_MODEL}...")
        _model = SentenceTransformer(EMBEDDING_MODEL)
        print("[MODEL] Ready.")
    return _model


def _chunk_text(text: str) -> List[dict]:
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


def _embed(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
    model = _get_model()
    passages = [f"passage: {t}" for t in texts]
    embeddings = model.encode(passages, normalize_embeddings=True)
    return [emb.tolist() for emb in embeddings]


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
