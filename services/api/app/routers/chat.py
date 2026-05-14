import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, Response
from psycopg.types.json import Json
from services.rag.app.guardrails import build_refusal_payload
from services.rag.app.main import answer as answer_rag
from services.rag.app.schemas import RagRequest

from ..core.config import settings
from ..core.db import get_db
from ..core.security import AuthUser, get_current_user
from ..core.users import ensure_user
from ..schemas import ChatRequest, ChatResponse

router = APIRouter()
log = logging.getLogger(__name__)


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


def _is_local_rag_url(url: str) -> bool:
    normalized = url.rstrip("/")
    return normalized.startswith("http://localhost") or normalized.startswith("http://127.0.0.1")


def _model_dump(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _answer_in_process(payload: Dict[str, Any]) -> tuple[Dict[str, Any], List[UUID], str]:
    response = Response()
    rag_response = answer_rag(RagRequest(**payload), response)
    retrieved_ids = _parse_uuid_list(response.headers.get("X-Retrieved-Chunk-Ids"))
    trace_id = response.headers.get("X-Trace-Id", str(uuid4()))
    return _model_dump(rag_response), retrieved_ids, trace_id


def _answer_via_rag(payload: Dict[str, Any], trace_id: str) -> tuple[Dict[str, Any], List[UUID], str]:
    if _is_local_rag_url(settings.rag_service_url):
        return _answer_in_process(payload)

    with httpx.Client(timeout=settings.request_timeout_seconds) as client:
        resp = client.post(
            f"{settings.rag_service_url}/rag/answer",
            json=payload,
            headers={"X-Trace-Id": trace_id},
        )
        resp.raise_for_status()
        data = resp.json()

    retrieved_ids = _parse_uuid_list(resp.headers.get("X-Retrieved-Chunk-Ids"))
    trace_id = resp.headers.get("X-Trace-Id", trace_id)
    return data, retrieved_ids, trace_id


def _unavailable_payload(language: str) -> Dict[str, Any]:
    payload = build_refusal_payload(language)
    payload["missing_info"] = (
        "The knowledge service or database is currently unavailable. "
        "Please check the deployment configuration and try again."
    )
    return payload


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
        data, retrieved_ids, trace_id = _answer_via_rag(payload, trace_id)
    except Exception as exc:
        log.exception("RAG answer failed: %s", exc)
        data = _unavailable_payload(request.language)
        retrieved_ids = []

    latency_ms = int((time.time() - start_time) * 1000)
    audit_log_id = None
    try:
        with get_db() as conn:
            user_id = ensure_user(conn, current_user)
            row = conn.execute(
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
                RETURNING id
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
            ).fetchone()
            audit_log_id = str(row["id"]) if row else None
    except Exception as exc:
        log.warning("Skipping audit log write: %s", exc)

    data["audit_log_id"] = audit_log_id
    return ChatResponse(**data)
