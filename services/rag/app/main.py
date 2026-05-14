from fastapi import FastAPI, Response

from .answering import answer_with_llm
from .core.config import settings
from .core.db import get_db
from .guardrails import build_meta
from .llm import get_provider
from .rag import filter_rows_by_doc_ids, filter_rows_by_status, get_accessible_document_ids, rerank_chunks, retrieve_chunks
from .schemas import RagRequest, StrictRagResponse

app = FastAPI(title=settings.app_name)


def _rewrite_question(question: str, history: list[tuple[str, str]], memory_summary: str, provider) -> str:
    if not history and not memory_summary:
        return question

    history_lines = []
    for role, text in history[-6:]:
        label = "User" if role == "user" else "Assistant"
        history_lines.append(f"{label}: {text}")

    prompt = "\n".join(
        [
            "Rewrite the current user question as a standalone search query for a banking knowledge base.",
            "Use the conversation history and user memory only to resolve references like 'that', 'it', or 'the second one'.",
            "Do not answer the question. Do not add facts that are not implied by the current question/history.",
            "Return only the rewritten search query, with no quotes or markdown.",
            "",
            "User memory:",
            memory_summary[:1000] if memory_summary else "(none)",
            "",
            "Conversation history:",
            "\n".join(history_lines) if history_lines else "(none)",
            "",
            f"Current question: {question}",
        ]
    )

    try:
        rewritten = provider.generate(
            "You rewrite follow-up questions into standalone retrieval queries.",
            prompt,
        ).strip()
    except Exception:
        return question

    rewritten = rewritten.strip("\"'` \n")
    if not rewritten or len(rewritten) > 500:
        return question
    return rewritten


@app.post("/rag/answer", response_model=StrictRagResponse)
def answer(request: RagRequest, response: Response) -> StrictRagResponse:
    provider = get_provider()
    history = [(h.role, h.text) for h in request.history] if request.history else []
    memory_summary = request.memory_summary or ""
    retrieval_question = _rewrite_question(request.question, history, memory_summary, provider)

    with get_db() as conn:
        allowed_doc_ids = get_accessible_document_ids(
            conn,
            request.user.role_names,
            request.user.attributes,
        )
        rows = retrieve_chunks(conn, retrieval_question, allowed_doc_ids, request.top_k)

    rows = filter_rows_by_doc_ids(rows, allowed_doc_ids)
    rows = filter_rows_by_status(rows)
    reranked = rerank_chunks(rows)

    payload, meta = answer_with_llm(
        reranked,
        request.question,
        request.language,
        request.user.role_names,
        provider,
        history=history,
        memory_summary=memory_summary,
    )
    if not meta.get("trace_id"):
        meta = build_meta(reranked)
    meta["retrieval_question"] = retrieval_question

    response.headers["X-Trace-Id"] = meta["trace_id"]
    response.headers["X-Retrieved-Chunk-Ids"] = ",".join(meta["retrieved_chunk_ids"])
    return StrictRagResponse(**payload)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
