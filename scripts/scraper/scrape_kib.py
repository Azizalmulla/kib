"""Scrape KIB website (public pages only)."""

import hashlib
import sys
import time

from .config import KIB_BASE_URL, KIB_SITEMAP_URL
from .discovery import discover_urls
from .extractor import detect_language, extract_text, extract_title
from .fetcher import fetch_html
from .direct_ingest import ingest_page

# KIB-specific exclusions
KIB_EXCLUDE = [
    "/online-banking",
    "/ebanking",
    "/login",
    "/apply",
    "/portal",
]

KIB_ACCESS_TAGS = {
    "source": "kib_website",
    "type": "internal_site",
    "tags": ["public", "internal_site", "kib"],
}


def _is_kib_excluded(url: str) -> bool:
    lower = url.lower()
    return any(pat in lower for pat in KIB_EXCLUDE)


def run() -> dict:
    print("=" * 60)
    print("KIB Website Scraper")
    print("=" * 60)

    urls = discover_urls(KIB_SITEMAP_URL, KIB_BASE_URL)

    scraped = 0
    ingested = 0
    skipped = 0
    errors = 0
    seen_hashes: set = set()

    for i, url in enumerate(urls, 1):
        if _is_kib_excluded(url):
            skipped += 1
            continue

        print(f"[{i}/{len(urls)}] {url}")

        html = fetch_html(url)
        if not html:
            skipped += 1
            continue

        text = extract_text(html)
        if not text:
            print("  [SKIP] Too little content")
            skipped += 1
            continue

        content_hash = hashlib.sha256(text.encode()).hexdigest()
        if content_hash in seen_hashes:
            print("  [SKIP] Duplicate content")
            skipped += 1
            continue
        seen_hashes.add(content_hash)

        title = extract_title(html)
        language = detect_language(text)
        scraped += 1

        result = ingest_page(
            text=text,
            title=title,
            source_uri=url,
            language=language,
            doc_type="web_page",
            access_tags=KIB_ACCESS_TAGS,
            allowed_roles="front_desk,compliance",
        )

        if result:
            ingested += 1
            print(f"  [OK] {title[:50]} ({language}) - {result.get('chunks_ingested', 0)} chunks")
        else:
            errors += 1

    summary = {
        "site": "KIB",
        "urls_discovered": len(urls),
        "scraped": scraped,
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
    }

    print("\n" + "=" * 60)
    print("KIB Scrape Summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("=" * 60)

    return summary


def main() -> int:
    summary = run()
    return 0 if summary["ingested"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
