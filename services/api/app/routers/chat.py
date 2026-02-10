import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Json

from ..core.config import settings
from ..core.db import get_db
from ..core.security import AuthUser, get_current_user
from ..core.users import ensure_user
from ..schemas import ChatRequest, ChatResponse

router = APIRouter()


def _parse_uuid_list(value: Optional[str]) -> List[UUID]:
    if not value:
        return []
    parsed: List[UUID] = []
    for raw in value.split(","):
        try:
            parsed.append(UUID(raw.strip()))
        except Exception:
            continue
    return parsed


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, current_user: AuthUser = Depends(get_current_user)) -> ChatResponse:
    payload: Dict[str, Any] = {
        "question": request.question,
        "language": request.language,
        "top_k": request.top_k,
        "user": {
            "id": str(current_user.subject),
            "role_names": current_user.roles,
            "attributes": current_user.attributes,
        },
        "history": [{"role": h.role, "text": h.text} for h in request.history[-6:]],
    }

    start_time = time.time()
    trace_id = str(uuid4())
    try:
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.post(
                f"{settings.rag_service_url}/rag/answer",
                json=payload,
                headers={"X-Trace-Id": trace_id},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="RAG service unavailable") from exc

    latency_ms = int((time.time() - start_time) * 1000)
    retrieved_ids = _parse_uuid_list(resp.headers.get("X-Retrieved-Chunk-Ids"))
    trace_id = resp.headers.get("X-Trace-Id", trace_id)

    with get_db() as conn:
        user_id = ensure_user(conn, current_user)
        conn.execute(
            """
            INSERT INTO audit_logs (
                user_id,
                role_names,
                query,
                request_language,
                response_language,
                retrieved_chunk_ids,
                answer,
                model_provider,
                model_name,
                model_version,
                retrieval_meta,
                trace_id,
                latency_ms
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                current_user.roles,
                request.question,
                request.language,
                data.get("language"),
                retrieved_ids,
                data.get("answer", ""),
                None,
                None,
                None,
                Json({
                    "confidence": data.get("confidence"),
                    "missing_info": data.get("missing_info"),
                }),
                trace_id,
                latency_ms,
            ),
        )

    return ChatResponse(**data)
