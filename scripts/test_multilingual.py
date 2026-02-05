import os
import sys

import httpx

INGEST_URL = os.getenv("KIB_INGEST_URL", "http://localhost:8001/ingest")
RAG_URL = os.getenv("KIB_RAG_URL", "http://localhost:8002/rag/answer")
ROLE = os.getenv("KIB_TEST_ROLE", "front_desk")

EN_TEXT = (
    "KIB offers personal savings accounts with monthly statements. "
    "Early withdrawal fees may apply based on the account terms."
)

AR_TEXT = (
    "يقدم بنك الكويت الدولي حسابات ادخار شخصية مع كشف حساب شهري. "
    "قد يتم تطبيق رسوم على السحب المبكر وفقًا لشروط الحساب."
)


def ingest(title: str, text: str, language: str) -> None:
    files = {"file": (f"{title}.txt", text.encode("utf-8"), "text/plain")}
    data = {
        "title": title,
        "doc_type": "policy",
        "language": language,
        "version": "v1",
        "allowed_roles": ROLE,
        "access_tags": "{}",
    }
    resp = httpx.post(INGEST_URL, data=data, files=files, timeout=60)
    resp.raise_for_status()
    print(f"Ingested {title} ({language}):", resp.json())


def query(question: str, language: str) -> None:
    payload = {
        "question": question,
        "language": language,
        "top_k": 3,
        "user": {"id": "test-user", "role_names": [ROLE], "attributes": {}},
    }
    resp = httpx.post(RAG_URL, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    print(f"\nQuery ({language}): {question}")
    print("Answer:", data.get("answer"))
    print("Citations:")
    for cite in data.get("citations", []):
        print("-", cite.get("quote"))


def main() -> int:
    try:
        ingest("kib_policy_en", EN_TEXT, "en")
        ingest("kib_policy_ar", AR_TEXT, "ar")
        query("What are the fees for early withdrawal?", "en")
        query("ما هي رسوم السحب المبكر؟", "ar")
    except httpx.HTTPError as exc:
        print("HTTP error:", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
