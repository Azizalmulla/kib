from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..core.config import settings
from ..core.db import get_db
from ..core.security import AuthUser, get_current_user
from ..schemas import AuditLogOut

router = APIRouter()


def _has_audit_access(roles: list[str]) -> bool:
    allowed = {r.strip() for r in settings.audit_read_roles.split(",") if r.strip()}
    return bool(set(roles) & allowed)


@router.get("/audit", response_model=list[AuditLogOut])
def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    user_id: Optional[str] = Query(default=None),
    current_user: AuthUser = Depends(get_current_user),
) -> list[AuditLogOut]:
    if not _has_audit_access(current_user.roles):
        raise HTTPException(status_code=403, detail="Insufficient role")

    params = []
    where = []
    if user_id:
        where.append("user_id = %s")
        params.append(user_id)

    where_clause = "" if not where else "WHERE " + " AND ".join(where)
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, user_id, role_names, query, retrieved_chunk_ids, answer,
                   model_name, model_version, created_at
            FROM audit_logs
            {where_clause}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        ).fetchall()

    return [AuditLogOut(**row) for row in rows]
