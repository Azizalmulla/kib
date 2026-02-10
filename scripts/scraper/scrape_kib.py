"""Scrape KIB website (public pages only)."""

import hashlib
import re
import sys
import time
from urllib.parse import urljoin

from .config import KIB_BASE_URL, KIB_SITEMAP_URL
from .discovery import discover_urls
from .extractor import detect_language, extract_text, extract_title
from .fetcher import fetch_html
from .direct_ingest import ingest_page, ingest_pdf
from .pdf_parser import download_pdf, extract_text_from_pdf, detect_pdf_language

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

PDF_URLS: set = set()


def _is_kib_excluded(url: str) -> bool:
    lower = url.lower()
    return any(pat in lower for pat in KIB_EXCLUDE)


def _is_pdf(url: str) -> bool:
    return url.lower().rstrip("/").endswith(".pdf")


def run() -> dict:
    print("=" * 60)
    print("KIB Website Scraper")
    print("=" * 60)

    urls = discover_urls(KIB_SITEMAP_URL, KIB_BASE_URL)

    scraped = 0
    ingested = 0
    skipped = 0
    errors = 0
    pdfs_ingested = 0
    pdfs_failed = 0
    seen_hashes: set = set()

    # Separate PDFs from HTML pages
    html_urls = []
    for url in urls:
        if _is_pdf(url):
            PDF_URLS.add(url)
        elif _is_kib_excluded(url):
            skipped += 1
        else:
            html_urls.append(url)

    print(f"[KIB] {len(html_urls)} HTML pages, {len(PDF_URLS)} PDFs discovered")

    # --- Phase 1: HTML pages ---
    for i, url in enumerate(html_urls, 1):
        print(f"[{i}/{len(html_urls)}] {url}")

        html = fetch_html(url)
        if not html:
            skipped += 1
            continue

        # Discover PDF links from the page
        for match in re.findall(r'href=["\']([^"\']*\.pdf)["\']', html, re.IGNORECASE):
            PDF_URLS.add(urljoin(url, match))

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

    # --- Phase 2: PDF ingestion ---
    if PDF_URLS:
        print(f"\n[KIB] Downloading and parsing {len(PDF_URLS)} PDFs...")
        for j, pdf_url in enumerate(sorted(PDF_URLS), 1):
            print(f"  [PDF {j}/{len(PDF_URLS)}] {pdf_url}")
            pdf_bytes = download_pdf(pdf_url)
            if not pdf_bytes:
                pdfs_failed += 1
                continue

            pages = extract_text_from_pdf(pdf_bytes)
            if not pages:
                print(f"    [SKIP] No extractable text")
                pdfs_failed += 1
                continue

            lang = detect_pdf_language(pages)
            filename = pdf_url.split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ")
            total_chars = sum(len(p["text"]) for p in pages)
            print(f"    {len(pages)} pages, {total_chars} chars, lang={lang}")

            result = ingest_pdf(
                pages=pages,
                title=filename,
                source_uri=pdf_url,
                language=lang,
                doc_type="pdf",
                access_tags=KIB_ACCESS_TAGS,
            )
            if result:
                pdfs_ingested += 1
                print(f"    [OK] {result['chunks_ingested']} chunks from {result['pages']} pages")
            else:
                pdfs_failed += 1
                print(f"    [ERROR] Ingestion returned None")

            time.sleep(0.5)

    summary = {
        "site": "KIB",
        "urls_discovered": len(urls),
        "scraped": scraped,
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "pdfs_ingested": pdfs_ingested,
        "pdfs_failed": pdfs_failed,
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
