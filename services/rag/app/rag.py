import math
from typing import Any, Dict, List

import httpx
from psycopg.types.json import Json

from .core.config import settings


def _truncate_normalize(vec: List[float], dim: int) -> List[float]:
    truncated = vec[:dim]
    norm = math.sqrt(sum(x * x for x in truncated))
    return [x / norm for x in truncated] if norm > 0 else truncated


def _embed_query(question: str) -> List[float]:
    resp = httpx.post(
        settings.fireworks_embed_url,
        json={
            "model": settings.embedding_model,
            "input": [question],
            "dimensions": settings.embedding_dim,
        },
        headers={"Authorization": f"Bearer {settings.fireworks_api_key}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def get_accessible_document_ids(
    conn,
    role_names: List[str],
    attributes: Dict[str, Any],
) -> List[str]:
    if not role_names:
        return []
    if attributes:
        rows = conn.execute(
            """
            SELECT DISTINCT d.id
            FROM documents d
            JOIN document_acl a ON a.document_id = d.id
            JOIN roles r ON r.id = a.role_id
            WHERE d.status = 'approved'
              AND r.name = ANY(%s)
              AND d.access_tags <@ %s::jsonb
            """,
            (role_names, Json(attributes)),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT d.id
            FROM documents d
            JOIN document_acl a ON a.document_id = d.id
            JOIN roles r ON r.id = a.role_id
            WHERE d.status = 'approved'
              AND r.name = ANY(%s)
            """,
            (role_names,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


def retrieve_chunks(
    conn,
    question: str,
    allowed_doc_ids: List[str],
    top_k: int,
) -> List[Dict[str, Any]]:
    if not allowed_doc_ids:
        return []

    query_vector = _embed_query(question)
    vec_str = "[" + ",".join(str(x) for x in query_vector) + "]"
    rows = conn.execute(
        """
        SELECT
            c.id AS chunk_id,
            c.text,
            c.page_start,
            c.page_end,
            c.section,
            c.offset_start,
            c.offset_end,
            dv.id AS document_version_id,
            dv.version AS document_version,
            d.id AS document_id,
            d.title AS document_title,
            d.status AS document_status,
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
        LIMIT %s
        """,
        (vec_str, allowed_doc_ids, settings.embedding_model, vec_str, top_k),
    ).fetchall()

    return rows


def filter_rows_by_doc_ids(
    rows: List[Dict[str, Any]],
    allowed_doc_ids: List[str],
) -> List[Dict[str, Any]]:
    allowed_set = {str(doc_id) for doc_id in allowed_doc_ids}
    return [row for row in rows if str(row.get("document_id")) in allowed_set]


def filter_rows_by_status(rows: List[Dict[str, Any]], status: str = "approved") -> List[Dict[str, Any]]:
    return [row for row in rows if row.get("document_status") == status]


def rerank_chunks(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Placeholder: replace with embedding or cross-encoder reranking.
    return rows
