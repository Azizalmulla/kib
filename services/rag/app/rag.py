import math
import re
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
    conn.execute(f"SET LOCAL ivfflat.probes = {settings.vector_probes}")
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


def _keyword_terms(question: str) -> List[str]:
    normalized = " ".join(question.split())
    lowered = normalized.lower()
    terms: List[str] = []

    def add(term: str) -> None:
        clean = " ".join(term.split())
        if clean and clean.lower() not in {item.lower() for item in terms}:
            terms.append(clean)

    add(normalized)

    for token in re.findall(r"[\w\u0600-\u06ff]+", normalized):
        if len(token) >= 3:
            add(token)

    domain_expansions = {
        "capital adequacy": [
            "capital adequacy",
            "CAR",
            "Capital Adequacy Ratio",
            "Basel III",
            "Pillar III",
            "Tier 1",
            "CET1",
        ],
        "كفاية رأس المال": [
            "كفاية رأس المال",
            "معيار كفاية رأس المال",
            "بازل",
            "Basel III",
            "Pillar III",
            "CAR",
            "Capital Adequacy Ratio",
        ],
        "online banking": [
            "online banking",
            "internet banking",
            "KIB Internet Banking",
            "terms and conditions online banking",
        ],
        "مكافحة غسل الأموال": [
            "مكافحة غسل الأموال",
            "AML",
            "Anti-money laundering",
            "Law No. (106)",
            "FATF",
        ],
    }

    for trigger, expansions in domain_expansions.items():
        if trigger.lower() in lowered or trigger in normalized:
            for expansion in expansions:
                add(expansion)

    return terms[:24]


def expand_retrieval_question(question: str) -> str:
    terms = _keyword_terms(question)
    return " ".join(terms[:12])[:700] or question


def retrieve_keyword_chunks(
    conn,
    question: str,
    allowed_doc_ids: List[str],
    top_k: int,
) -> List[Dict[str, Any]]:
    if not allowed_doc_ids:
        return []

    patterns = [f"%{term}%" for term in _keyword_terms(question)]
    if not patterns:
        return []

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
            0.25::float AS distance,
            (
                SELECT count(*)
                FROM unnest(%s::text[]) AS p(pattern)
                WHERE c.text ILIKE p.pattern
                   OR d.title ILIKE p.pattern
                   OR COALESCE(dv.source_uri, '') ILIKE p.pattern
            ) AS keyword_score
        FROM chunks c
        JOIN document_versions dv ON dv.id = c.document_version_id
        JOIN documents d ON d.id = dv.document_id
        WHERE d.id = ANY(%s)
          AND d.status = 'approved'
          AND dv.is_active = true
          AND EXISTS (
              SELECT 1
              FROM unnest(%s::text[]) AS p(pattern)
              WHERE c.text ILIKE p.pattern
                 OR d.title ILIKE p.pattern
                 OR COALESCE(dv.source_uri, '') ILIKE p.pattern
          )
        ORDER BY keyword_score DESC, d.title, c.page_start
        LIMIT %s
        """,
        (patterns, allowed_doc_ids, patterns, top_k),
    ).fetchall()

    return rows


def merge_chunk_rows(*row_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for rows in row_groups:
        for row in rows:
            key = str(row.get("chunk_id"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


def filter_rows_by_doc_ids(
    rows: List[Dict[str, Any]],
    allowed_doc_ids: List[str],
) -> List[Dict[str, Any]]:
    allowed_set = {str(doc_id) for doc_id in allowed_doc_ids}
    return [row for row in rows if str(row.get("document_id")) in allowed_set]


def filter_rows_by_status(rows: List[Dict[str, Any]], status: str = "approved") -> List[Dict[str, Any]]:
    return [row for row in rows if row.get("document_status") == status]


def rerank_chunks(question: str, rows: List[Dict[str, Any]], top_n: int | None = None) -> List[Dict[str, Any]]:
    if not rows:
        return []

    limit = top_n or settings.rerank_top_n
    if not settings.rerank_enabled:
        return rows[:limit]

    documents = []
    for row in rows:
        documents.append(
            "\n".join(
                [
                    f"Title: {row.get('document_title')}",
                    f"Page: {row.get('page_start')}",
                    str(row.get("text", ""))[:2500],
                ]
            )
        )

    try:
        resp = httpx.post(
            settings.fireworks_rerank_url,
            json={
                "model": settings.reranker_model,
                "query": question,
                "documents": documents,
                "top_n": min(limit, len(documents)),
                "return_documents": False,
                "task": "Rerank banking policy and financial document chunks for answering a user question.",
            },
            headers={"Authorization": f"Bearer {settings.fireworks_api_key}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return rows[:limit]

    reranked = []
    for item in data.get("results", []):
        index = item.get("index")
        if isinstance(index, int) and 0 <= index < len(rows):
            row = dict(rows[index])
            row["rerank_score"] = item.get("relevance_score")
            reranked.append(row)

    return reranked or rows[:limit]
