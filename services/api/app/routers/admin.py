import hashlib
import math
import os
import tempfile
from typing import List

import fitz  # PyMuPDF
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from psycopg.types.json import Json

from ..core.config import settings
from ..core.db import get_db
from ..core.security import AuthUser, get_current_user

router = APIRouter(prefix="/admin")

FIREWORKS_API_KEY = os.environ.get("FIREWORKS_API_KEY", "") or os.environ.get("KIB_FIREWORKS_API_KEY", "") or os.environ.get("KIB_LLM_API_KEY", "")
FIREWORKS_EMBED_URL = "https://api.fireworks.ai/inference/v1/embeddings"
EMBEDDING_MODEL = os.environ.get("KIB_EMBEDDING_MODEL", "accounts/fireworks/models/qwen3-embedding-8b")
EMBEDDING_DIM = int(os.environ.get("KIB_EMBEDDING_DIM", "768"))
CHUNK_SIZE = int(os.environ.get("KIB_CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.environ.get("KIB_CHUNK_OVERLAP", "100"))


def _require_admin(current_user: AuthUser):
    if "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Admin access required")


def _extract_pdf_pages(pdf_bytes: bytes) -> list[dict]:
    """Extract text from PDF page by page using PyMuPDF."""
    pages = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text().replace("\x00", "")
            if text.strip():
                pages.append({"page": i, "text": text})
    return pages


def _chunk_pages(pages: list[dict]) -> list[dict]:
    """Chunk multi-page text preserving page numbers."""
    all_chunks = []
    index = 0
    for p in pages:
        text = p["text"]
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


def _embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed a batch of texts using Fireworks API."""
    if not texts:
        return []
    resp = httpx.post(
        FIREWORKS_EMBED_URL,
        json={"model": EMBEDDING_MODEL, "input": texts, "dimensions": EMBEDDING_DIM},
        headers={"Authorization": f"Bearer {FIREWORKS_API_KEY}"},
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    sorted_data = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in sorted_data]


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    language: str = Form(default="en"),
    doc_type: str = Form(default="pdf"),
    allowed_roles: str = Form(default="admin,employee"),
    current_user: AuthUser = Depends(get_current_user),
):
    _require_admin(current_user)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(status_code=400, detail="File too large (max 50MB)")

    # Extract text
    try:
        pages = _extract_pdf_pages(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {str(e)}")

    if not pages:
        raise HTTPException(status_code=400, detail="No text could be extracted from this PDF")

    # Chunk
    chunks = _chunk_pages(pages)
    if not chunks:
        raise HTTPException(status_code=400, detail="No chunks generated")

    # Embed in batches of 32
    all_embeddings = []
    for b in range(0, len(chunks), 32):
        batch_texts = [c["text"] for c in chunks[b : b + 32]]
        try:
            all_embeddings.extend(_embed_batch(batch_texts))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Embedding failed: {str(e)}")

    # Store in database
    full_text = "\n".join(p["text"] for p in pages)
    sha256 = hashlib.sha256(full_text.encode()).hexdigest()
    role_names = [r.strip() for r in allowed_roles.split(",") if r.strip()]

    with get_db() as conn:
        # Check for duplicate
        existing = conn.execute(
            """SELECT dv.id FROM document_versions dv
               WHERE dv.sha256 = %s""",
            (sha256,),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="This document has already been uploaded")

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
            (title, doc_type, language, "approved", Json({})),
        ).fetchone()
        doc_id = doc_row["id"]

        # Create version
        ver_row = conn.execute(
            "INSERT INTO document_versions (document_id, version, source_uri, sha256, page_count) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (doc_id, "v1", f"upload://{file.filename}", sha256, len(pages)),
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
                (chunk_row["id"], all_embeddings[idx], EMBEDDING_MODEL),
            )

    return {
        "status": "success",
        "document_id": str(doc_id),
        "title": title,
        "pages": len(pages),
        "chunks": len(chunks),
        "message": f"Document '{title}' uploaded and ingested successfully ({len(pages)} pages, {len(chunks)} chunks)",
    }


@router.get("/documents")
def list_documents(
    current_user: AuthUser = Depends(get_current_user),
):
    _require_admin(current_user)

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.title, d.doc_type, d.language, d.status, d.created_at,
                   dv.page_count, dv.source_uri
            FROM documents d
            LEFT JOIN document_versions dv ON dv.document_id = d.id AND dv.is_active = true
            ORDER BY d.created_at DESC
            LIMIT 100
            """
        ).fetchall()

    return [dict(row) for row in rows]
