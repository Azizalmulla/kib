from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Json

from ..core.db import get_db
from ..core.security import AuthUser, get_current_user
from ..schemas import DocumentDetailResponse, DocumentOut, DocumentVersionOut

router = APIRouter()


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(
    language: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    current_user: AuthUser = Depends(get_current_user),
) -> list[DocumentOut]:
    roles = current_user.roles or []
    if not roles:
        return []

    params = [roles, Json(current_user.attributes or {})]
    filters = ["r.name = ANY(%s)", "d.status = 'approved'", "d.access_tags <@ %s::jsonb"]

    if language:
        filters.append("d.language = %s")
        params.append(language)
    if q:
        filters.append("d.title ILIKE %s")
        params.append(f"%{q}%")

    where_clause = " AND ".join(filters)

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT d.id, d.title, d.doc_type, d.language, d.status
            FROM documents d
            JOIN document_acl a ON a.document_id = d.id
            JOIN roles r ON r.id = a.role_id
            WHERE {where_clause}
            ORDER BY d.title
            """,
            params,
        ).fetchall()

    return [DocumentOut(**row) for row in rows]


@router.get("/documents/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: str,
    current_user: AuthUser = Depends(get_current_user),
) -> DocumentDetailResponse:
    roles = current_user.roles or []
    if not roles:
        raise HTTPException(status_code=404, detail="Document not found")

    with get_db() as conn:
        doc = conn.execute(
            """
            SELECT d.id, d.title, d.doc_type, d.language, d.status
            FROM documents d
            JOIN document_acl a ON a.document_id = d.id
            JOIN roles r ON r.id = a.role_id
            WHERE d.id = %s
              AND r.name = ANY(%s)
              AND d.status = 'approved'
              AND d.access_tags <@ %s::jsonb
            """,
            (document_id, roles, Json(current_user.attributes or {})),
        ).fetchone()

        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        version = conn.execute(
            """
            SELECT id, version, source_uri, page_count
            FROM document_versions
            WHERE document_id = %s AND is_active = true
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (document_id,),
        ).fetchone()

    return DocumentDetailResponse(
        document=DocumentOut(**doc),
        active_version=DocumentVersionOut(**version) if version else None,
    )
