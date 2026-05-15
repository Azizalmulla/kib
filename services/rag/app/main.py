import json
import re
from time import perf_counter

from fastapi import FastAPI, Response

from .answering import answer_with_llm
from .core.config import settings
from .core.db import get_db
from .guardrails import build_meta
from .llm import get_provider
from .rag import filter_rows_by_doc_ids, filter_rows_by_status, get_accessible_document_ids, rerank_chunks, retrieve_chunks
from .schemas import RagRequest, StrictRagResponse

app = FastAPI(title=settings.app_name)


FOLLOW_UP_PATTERNS = (
    r"\b(it|that|this|those|these|they|them|there|same|above|previous|last one|second one|first one)\b",
    r"\b(what about|how about|and for|compare|continue|explain more|tell me more|more details)\b",
    r"\b(its|their|them|that product|that policy|that document)\b",
)


def _should_rewrite_question(question: str, history: list[tuple[str, str]], memory_summary: str) -> bool:
    if not history and not memory_summary:
        return False

    normalized = " ".join(question.lower().split())
    if len(normalized.split()) <= 3 and history:
        return True

    return any(re.search(pattern, normalized) for pattern in FOLLOW_UP_PATTERNS)


def _last_user_turn(history: list[tuple[str, str]]) -> str:
    for role, text in reversed(history):
        if role == "user" and text.strip():
            return " ".join(text.split())[:300]
    return ""


def _rewrite_question(question: str, history: list[tuple[str, str]], memory_summary: str) -> str:
    if not _should_rewrite_question(question, history, memory_summary):
        return question

    last_user_turn = _last_user_turn(history)
    if last_user_turn:
        return f"{last_user_turn}. Follow-up question: {question}"[:500]

    if memory_summary and re.search(r"\b(previous|continue|last time|earlier|before)\b", question.lower()):
        return f"{memory_summary[:300]}. Follow-up question: {question}"[:500]

    return question


@app.post("/rag/answer", response_model=StrictRagResponse)
def answer(request: RagRequest, response: Response) -> StrictRagResponse:
    started_at = perf_counter()
    provider = get_provider()
    history = [(h.role, h.text) for h in request.history] if request.history else []
    memory_summary = request.memory_summary or ""
    rewrite_started_at = perf_counter()
    retrieval_question = _rewrite_question(request.question, history, memory_summary)
    rewrite_ms = int((perf_counter() - rewrite_started_at) * 1000)
    rewrite_used = retrieval_question != request.question
    answer_history = history if rewrite_used else []
    answer_memory_summary = memory_summary if rewrite_used else ""

    retrieval_started_at = perf_counter()
    with get_db() as conn:
        allowed_doc_ids = get_accessible_document_ids(
            conn,
            request.user.role_names,
            request.user.attributes,
        )
        rows = retrieve_chunks(conn, retrieval_question, allowed_doc_ids, request.top_k)
    retrieval_ms = int((perf_counter() - retrieval_started_at) * 1000)

    rows = filter_rows_by_doc_ids(rows, allowed_doc_ids)
    rows = filter_rows_by_status(rows)
    reranked = rerank_chunks(rows)

    answer_started_at = perf_counter()
    payload, meta = answer_with_llm(
        reranked,
        request.question,
        request.language,
        request.user.role_names,
        provider,
        history=answer_history,
        memory_summary=answer_memory_summary,
    )
    answer_ms = int((perf_counter() - answer_started_at) * 1000)
    if not meta.get("trace_id"):
        meta = build_meta(reranked)
    meta["retrieval_question"] = retrieval_question
    timings = {
        "rewrite_ms": rewrite_ms,
        "rewrite_used": rewrite_used,
        "retrieval_ms": retrieval_ms,
        "answer_ms": answer_ms,
        "total_ms": int((perf_counter() - started_at) * 1000),
    }

    response.headers["X-Trace-Id"] = meta["trace_id"]
    response.headers["X-Retrieved-Chunk-Ids"] = ",".join(meta["retrieved_chunk_ids"])
    response.headers["X-RAG-Timings"] = json.dumps(timings)
    return StrictRagResponse(**payload)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
