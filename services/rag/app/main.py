from fastapi import FastAPI, Response

from .answering import answer_with_llm
from .core.config import settings
from .core.db import get_db
from .guardrails import build_meta
from .llm import get_provider
from .rag import filter_rows_by_doc_ids, filter_rows_by_status, get_accessible_document_ids, rerank_chunks, retrieve_chunks
from .schemas import RagRequest, StrictRagResponse

app = FastAPI(title=settings.app_name)


@app.post("/rag/answer", response_model=StrictRagResponse)
def answer(request: RagRequest, response: Response) -> StrictRagResponse:
    with get_db() as conn:
        allowed_doc_ids = get_accessible_document_ids(
            conn,
            request.user.role_names,
            request.user.attributes,
        )
        rows = retrieve_chunks(conn, request.question, allowed_doc_ids, request.top_k)

    rows = filter_rows_by_doc_ids(rows, allowed_doc_ids)
    rows = filter_rows_by_status(rows)
    reranked = rerank_chunks(rows)

    provider = get_provider()
    payload, meta = answer_with_llm(
        reranked,
        request.question,
        request.language,
        request.user.role_names,
        provider,
    )

    if not meta.get("trace_id"):
        meta = build_meta(reranked)

    response.headers["X-Trace-Id"] = meta["trace_id"]
    response.headers["X-Retrieved-Chunk-Ids"] = ",".join(meta["retrieved_chunk_ids"])
    return StrictRagResponse(**payload)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
