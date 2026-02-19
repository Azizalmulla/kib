from fastapi import APIRouter, Depends, HTTPException

from ..core.db import get_db
from ..core.security import AuthUser, get_current_user
from ..core.users import ensure_user
from ..schemas import FeedbackOut, FeedbackRequest

router = APIRouter()


@router.post("/feedback", response_model=FeedbackOut)
def submit_feedback(
    request: FeedbackRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    with get_db() as conn:
        user_id = ensure_user(conn, current_user)

        # Verify audit log exists
        row = conn.execute(
            "SELECT id FROM audit_logs WHERE id = %s", (request.audit_log_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Audit log not found")

        result = conn.execute(
            """
            INSERT INTO feedback (audit_log_id, user_id, rating, correction)
            VALUES (%s, %s, %s, %s)
            RETURNING id, audit_log_id, rating, correction, created_at
            """,
            (request.audit_log_id, user_id, request.rating, request.correction),
        ).fetchone()

    return FeedbackOut(**result)


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(
    current_user: AuthUser = Depends(get_current_user),
):
    if "admin" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Admin access required")

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, audit_log_id, rating, correction, created_at
            FROM feedback
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()

    return [FeedbackOut(**row) for row in rows]
