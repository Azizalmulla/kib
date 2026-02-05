import json
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from services.rag.app.answering import answer_with_llm  # noqa: E402
from services.rag.app.llm import get_provider  # noqa: E402


def main() -> int:
    os.environ.setdefault("KIB_LLM_PROVIDER", "ollama")
    os.environ.setdefault("KIB_LLM_BASE_URL", "http://127.0.0.1:11434")
    os.environ.setdefault("KIB_LLM_MODEL", "llama3.1:8b")

    rows = [
        {
            "chunk_id": "33333333-3333-3333-3333-333333333333",
            "text": "KIB offers personal savings accounts with monthly statements. Early withdrawal fees may apply based on the account terms.",
            "document_title": "Savings Policy",
            "document_id": "doc-100",
            "document_version": "v1",
            "page_start": 3,
            "offset_start": 0,
            "offset_end": 120,
            "source_uri": "/docs/savings.pdf",
            "distance": 0.2,
        }
    ]

    provider = get_provider()
    payload, _ = answer_with_llm(
        rows,
        "What are the early withdrawal fees?",
        "en",
        ["front_desk"],
        provider,
    )

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not payload.get("citations"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
