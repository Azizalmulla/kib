import logging
import time
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from psycopg.types.json import Json
from services.rag.app.guardrails import build_refusal_payload
from services.rag.app.main import answer as answer_rag
from services.rag.app.schemas import RagRequest

from ..core.config import settings
from ..core.db import get_db
from ..core.security import AuthUser, get_current_user
from ..core.users import ensure_user
from ..schemas import ChatRequest, ChatResponse, ConversationOut

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


def _conversation_title(question: str) -> str:
    title = " ".join(question.split())
    return title[:57] + "..." if len(title) > 60 else title


def _load_memory_summary(conn, user_id: UUID) -> str:
    row = conn.execute(
        "SELECT summary FROM user_memory WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    return (row or {}).get("summary") or ""


def _ensure_conversation(conn, user_id: UUID, request: ChatRequest, memory_summary: str) -> UUID:
    if request.conversation_id:
        row = conn.execute(
            "SELECT id FROM chat_conversations WHERE id = %s AND user_id = %s",
            (request.conversation_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return row["id"]

    row = conn.execute(
        """
        INSERT INTO chat_conversations (user_id, title, memory_snapshot)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (user_id, _conversation_title(request.question), memory_summary or None),
    ).fetchone()
    return row["id"]


def _load_history(conn, conversation_id: UUID, limit: int = 12) -> List[Dict[str, str]]:
    rows = conn.execute(
        """
        SELECT role, text
        FROM chat_messages
        WHERE conversation_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (conversation_id, limit),
    ).fetchall()
    return [{"role": row["role"], "text": row["text"]} for row in reversed(rows)]


def _store_message(
    conn,
    conversation_id: UUID,
    role: str,
    text: str,
    response_payload: Optional[Dict[str, Any]] = None,
    audit_log_id: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO chat_messages (conversation_id, role, text, response, audit_log_id)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            conversation_id,
            role,
            text,
            Json(response_payload) if response_payload is not None else None,
            audit_log_id,
        ),
    )
    conn.execute(
        "UPDATE chat_conversations SET updated_at = now() WHERE id = %s",
        (conversation_id,),
    )


def _update_user_memory(conn, user_id: UUID, language: str) -> None:
    rows = conn.execute(
        """
        SELECT m.text
        FROM chat_messages m
        JOIN chat_conversations c ON c.id = m.conversation_id
        WHERE c.user_id = %s
          AND m.role = 'user'
        ORDER BY m.created_at DESC
        LIMIT 8
        """,
        (user_id,),
    ).fetchall()
    topics = []
    for row in rows:
        text = " ".join(str(row["text"]).split())
        if text:
            topics.append(text[:160])

    preferred = "Arabic" if language == "ar" else "English"
    if topics:
        summary = f"Preferred language: {preferred}. Recent user topics: " + "; ".join(reversed(topics))
    else:
        summary = f"Preferred language: {preferred}."
    summary = summary[:1200]

    conn.execute(
        """
        INSERT INTO user_memory (user_id, summary, preferred_language, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (user_id)
        DO UPDATE SET
            summary = EXCLUDED.summary,
            preferred_language = EXCLUDED.preferred_language,
            updated_at = now()
        """,
        (user_id, summary, language),
    )


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    limit: int = Query(default=20, ge=1, le=50),
    current_user: AuthUser = Depends(get_current_user),
) -> list[ConversationOut]:
    with get_db() as conn:
        user_id = ensure_user(conn, current_user)
        conversations = conn.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM chat_conversations
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()

        if not conversations:
            return []

        ids = [row["id"] for row in conversations]
        messages = conn.execute(
            """
            SELECT id, conversation_id, role, text, response, audit_log_id, created_at
            FROM chat_messages
            WHERE conversation_id = ANY(%s)
            ORDER BY created_at ASC
            """,
            (ids,),
        ).fetchall()

    by_conversation: Dict[UUID, List[Dict[str, Any]]] = {row["id"]: [] for row in conversations}
    for message in messages:
        message_dict = dict(message)
        conversation_id = message_dict.pop("conversation_id")
        by_conversation.setdefault(conversation_id, []).append(message_dict)

    return [
        ConversationOut(
            **dict(row),
            messages=by_conversation.get(row["id"], []),
        )
        for row in conversations
    ]


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: UUID,
    current_user: AuthUser = Depends(get_current_user),
) -> dict:
    with get_db() as conn:
        user_id = ensure_user(conn, current_user)
        result = conn.execute(
            "DELETE FROM chat_conversations WHERE id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, current_user: AuthUser = Depends(get_current_user)) -> ChatResponse:
    with get_db() as conn:
        user_id = ensure_user(conn, current_user)
        memory_summary = _load_memory_summary(conn, user_id)
        conversation_id = _ensure_conversation(conn, user_id, request, memory_summary)
        history = _load_history(conn, conversation_id)
        _store_message(conn, conversation_id, "user", request.question)

    if not history and request.history:
        history = [{"role": h.role, "text": h.text} for h in request.history[-8:]]

    payload: Dict[str, Any] = {
        "question": request.question,
        "language": request.language,
        "top_k": request.top_k,
        "user": {
            "id": str(current_user.subject),
            "role_names": current_user.roles,
            "attributes": current_user.attributes,
        },
        "history": history[-8:],
        "memory_summary": memory_summary,
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
                        "conversation_id": str(conversation_id),
                        "memory_used": bool(memory_summary),
                    }),
                    trace_id,
                    latency_ms,
                ),
            ).fetchone()
            audit_log_id = str(row["id"]) if row else None
            response_payload = dict(data)
            response_payload["audit_log_id"] = audit_log_id
            response_payload["conversation_id"] = str(conversation_id)
            _store_message(
                conn,
                conversation_id,
                "assistant",
                data.get("answer", ""),
                response_payload=response_payload,
                audit_log_id=audit_log_id,
            )
            _update_user_memory(conn, user_id, data.get("language") or request.language)
    except Exception as exc:
        log.warning("Skipping audit log write: %s", exc)

    data["audit_log_id"] = audit_log_id
    data["conversation_id"] = str(conversation_id)
    return ChatResponse(**data)
