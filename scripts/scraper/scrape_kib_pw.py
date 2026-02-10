"""Scrape KIB website using Playwright (site blocks non-browser requests)."""

import hashlib
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

from .config import KIB_BASE_URL, REQUEST_DELAY_SECONDS, MAX_PAGES_PER_SITE
from .extractor import detect_language, extract_text, extract_title
from .direct_ingest import ingest_page, ingest_pdf
from .pdf_parser import download_pdf, extract_text_from_pdf, detect_pdf_language

KIB_EXCLUDE = [
    "/online-banking",
    "/ebanking",
    "/login",
    "/apply",
    "/portal",
    "/admin",
    "/search",
    "/print",
    "#",
    "javascript:",
    "mailto:",
    "tel:",
]

MIN_TEXT_LENGTH = 50
MAX_RETRIES = 2
RETRY_TIMEOUT = 60000

KIB_ACCESS_TAGS = {
    "source": "kib_website",
    "type": "internal_site",
    "tags": ["public", "internal_site", "kib"],
}

SEEN_HASHES: set = set()
PDF_URLS: set = set()


def _is_pdf(url: str) -> bool:
    return url.lower().rstrip("/").endswith(".pdf")


def _is_excluded(url: str) -> bool:
    lower = url.lower()
    if _is_pdf(url):
        return False
    if any(pat in lower for pat in KIB_EXCLUDE):
        return True
    path = urlparse(lower).path.rstrip("/")
    if path in ("", "/"):
        return False
    return False


def _same_domain(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc in ("www.kib.com.kw", "kib.com.kw")


def _collect_link(link: str, discovered: set, urls: list):
    """Classify a discovered link as PDF or HTML page."""
    clean = link.split("#")[0].split("?")[0].rstrip("/")
    if not clean:
        return
    if _is_pdf(clean):
        PDF_URLS.add(clean)
    elif not _is_excluded(clean) and clean not in discovered:
        discovered.add(clean)
        if len(urls) < MAX_PAGES_PER_SITE:
            urls.append(clean)


def run() -> dict:
    stats = {
        "site": "KIB", "urls_discovered": 0, "scraped": 0,
        "ingested": 0, "skipped": 0, "errors": 0,
        "pdfs_ingested": 0, "pdfs_failed": 0,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(30000)

        # Load homepage
        print("[KIB-PW] Loading homepage...")
        try:
            page.goto(KIB_BASE_URL, wait_until="networkidle", timeout=60000)
            time.sleep(3)
        except Exception as e:
            print(f"[KIB-PW] Failed to load homepage: {e}")
            browser.close()
            return stats

        # Grab all internal links
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
        )

        discovered = set()
        discovered.add(KIB_BASE_URL)
        urls = [KIB_BASE_URL]
        for link in links:
            if _same_domain(link):
                _collect_link(link, discovered, urls)

        # Known important KIB paths (EN + AR)
        known_paths = [
            "/en/personal",
            "/en/corporate",
            "/en/about-us",
            "/en/about-us/overview",
            "/en/about-us/board-of-directors",
            "/en/about-us/management-team",
            "/en/about-us/shariah-supervisory-board",
            "/en/about-us/corporate-governance",
            "/en/about-us/investor-relations",
            "/en/about-us/careers",
            "/en/about-us/contact-us",
            "/en/personal/accounts",
            "/en/personal/cards",
            "/en/personal/financing",
            "/en/personal/deposits",
            "/en/personal/digital-banking",
            "/en/corporate/accounts",
            "/en/corporate/financing",
            "/en/corporate/trade-finance",
            "/en/corporate/treasury",
            "/en/faq",
            "/en/terms-and-conditions",
            "/en/privacy-policy",
            "/en/complaints",
            "/en/fees-and-charges",
            "/ar/personal",
            "/ar/corporate",
            "/ar/about-us",
            "/ar/about-us/overview",
            "/ar/about-us/board-of-directors",
            "/ar/about-us/management-team",
            "/ar/about-us/shariah-supervisory-board",
            "/ar/about-us/corporate-governance",
            "/ar/about-us/investor-relations",
            "/ar/about-us/careers",
            "/ar/about-us/contact-us",
            "/ar/personal/accounts",
            "/ar/personal/cards",
            "/ar/personal/financing",
            "/ar/personal/deposits",
            "/ar/personal/digital-banking",
            "/ar/corporate/accounts",
            "/ar/corporate/financing",
            "/ar/corporate/trade-finance",
            "/ar/corporate/treasury",
            "/ar/faq",
            "/ar/terms-and-conditions",
            "/ar/privacy-policy",
            "/ar/complaints",
            "/ar/fees-and-charges",
        ]
        for path in known_paths:
            full = KIB_BASE_URL.rstrip("/") + path
            _collect_link(full, discovered, urls)

        urls = sorted(set(urls))[:MAX_PAGES_PER_SITE]
        stats["urls_discovered"] = len(urls)
        print(f"[KIB-PW] Discovered {len(urls)} HTML URLs + {len(PDF_URLS)} PDFs")

        # --- Phase 1: HTML pages ---
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")

            html = None
            resp = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    timeout = 30000 if attempt == 1 else RETRY_TIMEOUT
                    resp = page.goto(url, wait_until="networkidle", timeout=timeout)
                    time.sleep(2)
                    html = page.content()
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        print(f"  [RETRY {attempt}] {e}")
                        time.sleep(3)
                    else:
                        stats["errors"] += 1
                        print(f"  [ERROR] {e} (after {MAX_RETRIES} attempts)")

            if html is None:
                continue

            if resp and resp.status >= 400:
                stats["errors"] += 1
                print(f"  [ERROR] HTTP {resp.status}")
                continue

            # Discover sub-links + PDFs
            try:
                sub_links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
                )
                for link in sub_links:
                    if _same_domain(link):
                        _collect_link(link, discovered, urls)
            except Exception:
                pass

            # Also find PDF links in href attributes
            try:
                pdf_links = page.eval_on_selector_all(
                    "a[href$='.pdf'], a[href$='.PDF']",
                    "els => els.map(e => e.href)"
                )
                for pl in pdf_links:
                    PDF_URLS.add(pl.split("?")[0].split("#")[0])
            except Exception:
                pass

            # Extract & ingest
            text = extract_text(html)
            if not text or len(text) < MIN_TEXT_LENGTH:
                stats["skipped"] += 1
                print(f"  [SKIP] Too little content ({len(text) if text else 0} chars)")
                continue

            content_hash = hashlib.sha256(text.encode()).hexdigest()
            if content_hash in SEEN_HASHES:
                stats["skipped"] += 1
                print("  [SKIP] Duplicate content")
                continue
            SEEN_HASHES.add(content_hash)

            title = extract_title(html)[:80] if extract_title(html) else url.split("/")[-1]
            lang = detect_language(text)
            stats["scraped"] += 1

            result = ingest_page(
                text=text,
                title=title,
                source_uri=url,
                language=lang,
                doc_type="web_page",
                access_tags=KIB_ACCESS_TAGS,
            )

            if result:
                stats["ingested"] += 1
                print(f"  [OK] {title[:60]} ({lang}) - {result.get('chunks_ingested', 0)} chunks")
            else:
                stats["errors"] += 1
                print("  [ERROR] Ingestion returned None")

            time.sleep(REQUEST_DELAY_SECONDS)

        browser.close()

    # --- Phase 2: PDF ingestion ---
    if PDF_URLS:
        print(f"\n[KIB-PW] Downloading and parsing {len(PDF_URLS)} PDFs...")
        for j, pdf_url in enumerate(sorted(PDF_URLS), 1):
            print(f"  [PDF {j}/{len(PDF_URLS)}] {pdf_url}")
            pdf_bytes = download_pdf(pdf_url)
            if not pdf_bytes:
                stats["pdfs_failed"] += 1
                continue

            pages = extract_text_from_pdf(pdf_bytes)
            if not pages:
                print(f"    [SKIP] No extractable text")
                stats["pdfs_failed"] += 1
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
                stats["pdfs_ingested"] += 1
                print(f"    [OK] {result['chunks_ingested']} chunks from {result['pages']} pages")
            else:
                stats["pdfs_failed"] += 1
                print(f"    [ERROR] Ingestion returned None")

            time.sleep(0.5)

    return stats


def main() -> int:
    summary = run()
    return 0 if summary["ingested"] > 0 else 1


if __name__ == "__main__":
    print("=" * 60)
    print("KIB Website Scraper (Playwright)")
    print("=" * 60)
    result = run()
    print("\n" + "=" * 60)
    print("KIB Scrape Summary:")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("=" * 60)
