import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3]))

from services.rag.app.answering import answer_with_llm  # noqa: E402
from services.rag.app.guardrails import REFUSAL_TEXT_EN  # noqa: E402
from services.rag.app.llm import MockProvider  # noqa: E402
from services.rag.app.rag import filter_rows_by_doc_ids  # noqa: E402
from services.rag.app.schemas import StrictRagResponse  # noqa: E402


def _row(text: str, distance: float, doc_id: str) -> dict:
    return {
        "chunk_id": "11111111-1111-1111-1111-111111111111",
        "text": text,
        "document_title": "Savings Policy",
        "document_id": doc_id,
        "document_version": "v1",
        "page_start": 2,
        "offset_start": 10,
        "offset_end": 60,
        "source_uri": "/docs/savings.pdf",
        "distance": distance,
    }


def _llm_json(doc_id: str) -> str:
    payload = {
        "language": "en",
        "answer": "KIB offers personal savings accounts with monthly statements.",
        "confidence": "high",
        "citations": [
            {
                "doc_title": "Savings Policy",
                "doc_id": doc_id,
                "document_version": "v1",
                "page_number": 2,
                "start_offset": 10,
                "end_offset": 60,
                "quote": "KIB offers personal savings accounts with monthly statements.",
                "source_uri": "/docs/savings.pdf",
            }
        ],
        "missing_info": None,
        "safe_next_steps": ["Search by policy or product name."],
    }
    return json.dumps(payload)


def test_grounded_answer_with_citations():
    rows = [_row("KIB offers personal savings accounts with monthly statements.", 0.2, "doc-1")]
    provider = MockProvider(_llm_json("doc-1"))
    payload, _ = answer_with_llm(rows, "What is offered?", "en", ["front_desk"], provider)
    StrictRagResponse(**payload)

    assert payload["citations"]
    assert payload["missing_info"] is None
    assert payload["confidence"] in {"high", "medium"}

    quote = payload["citations"][0]["quote"]
    assert len(quote.split()) <= 25


def test_empty_retrieval_refusal():
    provider = MockProvider("{}")
    payload, _ = answer_with_llm([], "What is the fee?", "en", [], provider)
    assert payload["answer"] == REFUSAL_TEXT_EN
    assert payload["citations"] == []
    assert payload["confidence"] == "low"
    assert payload["missing_info"]


def test_weak_evidence_low_confidence():
    rows = [_row("KIB offers services.", 0.95, "doc-1")]
    provider = MockProvider(_llm_json("doc-1"))
    payload, _ = answer_with_llm(rows, "Explain fees", "en", ["front_desk"], provider)
    assert payload["confidence"] == "low"
    assert payload["missing_info"]


def test_arabic_query_arabic_answer():
    arabic_text = "يقدم بنك الكويت الدولي حسابات ادخار شخصية مع كشف حساب شهري."
    rows = [_row(arabic_text, 0.2, "doc-1")]
    payload_json = {
        "language": "ar",
        "answer": "يقدم بنك الكويت الدولي حسابات ادخار شخصية مع كشف حساب شهري.",
        "confidence": "high",
        "citations": [
            {
                "doc_title": "سياسة الادخار",
                "doc_id": "doc-1",
                "document_version": "v1",
                "page_number": 2,
                "start_offset": 10,
                "end_offset": 60,
                "quote": arabic_text,
                "source_uri": "/docs/savings.pdf",
            }
        ],
        "missing_info": None,
        "safe_next_steps": ["ابحث باسم السياسة أو المنتج."],
    }
    provider = MockProvider(json.dumps(payload_json))
    payload, _ = answer_with_llm(rows, "ما هي الحسابات المتاحة؟", "ar", ["front_desk"], provider)

    assert payload["language"] == "ar"
    assert payload["answer"].startswith("يقدم بنك الكويت الدولي")
    assert arabic_text.split()[0] in payload["citations"][0]["quote"]


def test_unauthorized_chunks_filtered():
    rows = [
        _row("Allowed text.", 0.2, "allowed-doc"),
        _row("Unauthorized text.", 0.2, "denied-doc"),
    ]
    filtered = filter_rows_by_doc_ids(rows, ["allowed-doc"])
    assert len(filtered) == 1
    assert filtered[0]["document_id"] == "allowed-doc"
