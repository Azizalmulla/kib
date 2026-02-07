import hashlib
import json
import os
from typing import List, Optional
from uuid import uuid4

from fastapi import FastAPI, File, Form, UploadFile

from .core.config import settings
from .core.db import get_db
from .pipeline import chunk_text, embed_texts, parse_document

app = FastAPI(title=settings.app_name)


def _ensure_role(conn, role_name: str) -> str:
    row = conn.execute(
        """
        INSERT INTO roles (name)
        VALUES (%s)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        (role_name,),
    ).fetchone()
    return row["id"]


def _create_document(
    conn,
    title: str,
    doc_type: Optional[str],
    language: str,
    status: str,
    access_tags: dict,
) -> str:
    row = conn.execute(
        """
        INSERT INTO documents (title, doc_type, language, status, access_tags)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (title, doc_type, language, status, access_tags),
    ).fetchone()
    return row["id"]


def _create_document_version(
    conn,
    document_id: str,
    version: str,
    source_uri: str,
    sha256: str,
    page_count: Optional[int],
) -> str:
    row = conn.execute(
        """
        INSERT INTO document_versions (document_id, version, source_uri, sha256, page_count)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (document_id, version, source_uri, sha256, page_count),
    ).fetchone()
    return row["id"]


def _grant_access(conn, document_id: str, role_ids: List[str]) -> None:
    for role_id in role_ids:
        conn.execute(
            """
            INSERT INTO document_acl (document_id, role_id)
            VALUES (%s, %s)
            ON CONFLICT (document_id, role_id) DO NOTHING
            """,
            (document_id, role_id),
        )


@app.post("/ingest")
async def ingest(
    file: UploadFile = File(...),
    title: str = Form(...),
    doc_type: Optional[str] = Form(default=None),
    language: str = Form(default="en"),
    version: str = Form(default="v1"),
    status: str = Form(default="approved"),
    allowed_roles: str = Form(default="front_desk"),
    access_tags: str = Form(default="{}"),
    source_uri: Optional[str] = Form(default=None),
) -> dict:
    content = await file.read()
    os.makedirs(settings.uploads_dir, exist_ok=True)

    file_id = uuid4().hex
    safe_name = file.filename or "upload"
    dest_path = os.path.join(settings.uploads_dir, f"{file_id}_{safe_name}")
    with open(dest_path, "wb") as handle:
        handle.write(content)

    sha256 = hashlib.sha256(content).hexdigest()
    text, meta = parse_document(safe_name, content)
    chunks = chunk_text(text)
    should_index = status == "approved"
    embeddings = embed_texts([chunk["text"] for chunk in chunks]) if should_index else []

    role_names = [r.strip() for r in allowed_roles.split(",") if r.strip()]
    try:
        parsed_access_tags = json.loads(access_tags) if access_tags else {}
    except json.JSONDecodeError:
        parsed_access_tags = {}

    with get_db() as conn:
        role_ids = [_ensure_role(conn, role_name) for role_name in role_names]
        document_id = _create_document(conn, title, doc_type, language, status, parsed_access_tags)
        version_id = _create_document_version(
            conn,
            document_id,
            version,
            source_uri or dest_path,
            sha256,
            meta.get("page_count"),
        )
        _grant_access(conn, document_id, role_ids)

        for idx, chunk in enumerate(chunks):
            row = conn.execute(
                """
                INSERT INTO chunks (
                    document_version_id,
                    chunk_index,
                    text,
                    page_start,
                    page_end,
                    offset_start,
                    offset_end,
                    hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    version_id,
                    chunk["chunk_index"],
                    chunk["text"],
                    chunk.get("page_start"),
                    chunk.get("page_end"),
                    chunk["offset_start"],
                    chunk["offset_end"],
                    chunk["hash"],
                ),
            ).fetchone()

            if should_index:
                conn.execute(
                    """
                    INSERT INTO embeddings (chunk_id, embedding, model)
                    VALUES (%s, %s, %s)
                    """,
                    (row["id"], embeddings[idx], settings.embedding_model),
                )

    return {
        "document_id": document_id,
        "document_version_id": version_id,
        "chunks_ingested": len(chunks),
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
