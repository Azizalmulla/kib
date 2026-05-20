import json
import re
from time import perf_counter

from fastapi import FastAPI, Response

from .answering import answer_with_llm
from .core.config import settings
from .core.db import get_db
from .guardrails import build_meta
from .llm import get_provider
from .rag import (
    expand_retrieval_question,
    filter_rows_by_doc_ids,
    filter_rows_by_status,
    get_accessible_document_ids,
    merge_chunk_rows,
    rerank_chunks,
    retrieve_chunks,
    retrieve_keyword_chunks,
)
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


def _is_refusal_payload(payload: dict) -> bool:
    answer = str(payload.get("answer") or "").strip()
    return answer in {
        "I can't answer from KIB's approved documents for this question.",
        "لا أستطيع الإجابة من مستندات KIB المعتمدة لهذا السؤال.",
    } or (payload.get("confidence") == "low" and not payload.get("citations"))


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
    candidate_k = max(request.top_k, settings.rerank_candidate_k) if settings.rerank_enabled else request.top_k
    with get_db() as conn:
        allowed_doc_ids = get_accessible_document_ids(
            conn,
            request.user.role_names,
            request.user.attributes,
        )
        rows = retrieve_chunks(conn, retrieval_question, allowed_doc_ids, candidate_k)
    retrieval_ms = int((perf_counter() - retrieval_started_at) * 1000)

    rows = filter_rows_by_doc_ids(rows, allowed_doc_ids)
    rows = filter_rows_by_status(rows)
    rerank_started_at = perf_counter()
    reranked = rerank_chunks(retrieval_question, rows, settings.rerank_top_n)
    rerank_ms = int((perf_counter() - rerank_started_at) * 1000)

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
    recovery_used = False
    recovery_ms = 0

    if _is_refusal_payload(payload):
        recovery_started_at = perf_counter()
        expanded_question = expand_retrieval_question(retrieval_question)
        recovery_candidate_k = max(candidate_k, settings.recovery_candidate_k)
        with get_db() as conn:
            recovery_vector_rows = retrieve_chunks(
                conn,
                expanded_question,
                allowed_doc_ids,
                recovery_candidate_k,
            )
            keyword_rows = retrieve_keyword_chunks(
                conn,
                expanded_question,
                allowed_doc_ids,
                settings.keyword_candidate_k,
            )

        recovery_rows = merge_chunk_rows(keyword_rows, recovery_vector_rows, rows)
        recovery_rows = filter_rows_by_doc_ids(recovery_rows, allowed_doc_ids)
        recovery_rows = filter_rows_by_status(recovery_rows)
        recovery_reranked = rerank_chunks(expanded_question, recovery_rows, settings.rerank_top_n)
        recovery_payload, recovery_meta = answer_with_llm(
            recovery_reranked,
            request.question,
            request.language,
            request.user.role_names,
            provider,
            history=answer_history,
            memory_summary=answer_memory_summary,
        )
        recovery_ms = int((perf_counter() - recovery_started_at) * 1000)
        if not _is_refusal_payload(recovery_payload):
            payload = recovery_payload
            meta = recovery_meta
            reranked = recovery_reranked
            retrieval_question = expanded_question
            recovery_used = True

    if not meta.get("trace_id"):
        meta = build_meta(reranked)
    meta["retrieval_question"] = retrieval_question
    timings = {
        "rewrite_ms": rewrite_ms,
        "rewrite_used": rewrite_used,
        "retrieval_ms": retrieval_ms,
        "rerank_ms": rerank_ms,
        "candidate_k": candidate_k,
        "final_k": len(reranked),
        "recovery_used": recovery_used,
        "recovery_ms": recovery_ms,
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
