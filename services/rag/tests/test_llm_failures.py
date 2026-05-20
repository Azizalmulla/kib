import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from services.rag.app.answering import _build_user_prompt, answer_with_llm  # noqa: E402
from services.rag.app.guardrails import REFUSAL_TEXT_EN  # noqa: E402
from services.rag.app.llm import MockProvider  # noqa: E402
from services.rag.app.rag import filter_rows_by_status  # noqa: E402


def _row(text: str, distance: float, doc_id: str, status: str = "approved") -> dict:
    return {
        "chunk_id": "22222222-2222-2222-2222-222222222222",
        "text": text,
        "document_title": "Savings Policy",
        "document_id": doc_id,
        "document_version": "v1",
        "page_start": 2,
        "offset_start": 10,
        "offset_end": 60,
        "source_uri": "/docs/savings.pdf",
        "distance": distance,
        "document_status": status,
    }


def test_invalid_json_from_llm_signals_answer_failure():
    rows = [_row("KIB offers personal savings accounts.", 0.2, "doc-1")]
    provider = MockProvider("not-json")
    payload, meta = answer_with_llm(rows, "What is offered?", "en", ["front_desk"], provider)
    assert payload["answer"] == REFUSAL_TEXT_EN
    assert "model is temporarily unavailable" in (payload["missing_info"] or "").lower()
    assert meta.get("llm_error") == "json_parse_failed"


def test_citation_with_wrong_doc_id_is_corrected_to_retrieved_row():
    rows = [_row("KIB offers personal savings accounts.", 0.2, "doc-1")]
    bad = {
        "language": "en",
        "answer": "KIB offers personal savings accounts.",
        "confidence": "high",
        "citations": [
            {
                "doc_title": "Other Policy",
                "doc_id": "doc-999",
                "document_version": "v1",
                "page_number": 2,
                "start_offset": 10,
                "end_offset": 60,
                "quote": "KIB offers personal savings accounts.",
                "source_uri": "/docs/other.pdf",
            }
        ],
        "missing_info": None,
        "safe_next_steps": ["Search by policy or product name."],
    }
    provider = MockProvider(json.dumps(bad))
    payload, _ = answer_with_llm(rows, "What is offered?", "en", ["front_desk"], provider)
    assert payload["answer"] == "KIB offers personal savings accounts."
    assert payload["citations"], "expected the citation to be remapped to the retrieved row"
    assert payload["citations"][0]["doc_id"] == "doc-1"


def test_unapproved_docs_filtered_out():
    rows = [_row("Draft text.", 0.2, "doc-1", status="draft")]
    filtered = filter_rows_by_status(rows, status="approved")
    assert filtered == []


def test_answer_prompt_uses_user_facing_evidence_language():
    prompt = _build_user_prompt(
        "What are the AML laws?",
        "en",
        ["front_desk"],
        [_row("Law No. (106) of 2013 concerns AML/CFT.", 0.2, "doc-1")],
    )

    assert "Evidence excerpts:" in prompt
    assert "Retrieved chunks:" not in prompt
    assert "based only on the chunks" not in prompt
    assert "provided documents" not in prompt
