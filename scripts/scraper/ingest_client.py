"""Client to POST scraped pages to the ingestion service."""

import hashlib
import json
from typing import Optional

import httpx

from .config import INGEST_URL, REQUEST_TIMEOUT


def ingest_page(
    text: str,
    title: str,
    source_uri: str,
    language: str,
    doc_type: str = "web_page",
    access_tags: Optional[dict] = None,
    allowed_roles: str = "front_desk",
) -> Optional[dict]:
    content_bytes = text.encode("utf-8")
    sha = hashlib.sha256(content_bytes).hexdigest()[:12]
    filename = f"scraped_{sha}.txt"

    tags = json.dumps(access_tags or {})

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(
                INGEST_URL,
                files={"file": (filename, content_bytes, "text/plain")},
                data={
                    "title": title,
                    "doc_type": doc_type,
                    "language": language,
                    "version": "v1",
                    "status": "approved",
                    "allowed_roles": allowed_roles,
                    "access_tags": tags,
                    "source_uri": source_uri,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        print(f"  [INGEST ERROR] {source_uri}: {exc}")
        return None
