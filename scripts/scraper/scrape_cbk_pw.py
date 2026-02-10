"""Scrape Central Bank of Kuwait website using Playwright (JS-rendered SPA)."""

import hashlib
import sys
import time
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

from .config import CBK_BASE_URL, REQUEST_DELAY_SECONDS, MAX_PAGES_PER_SITE
from .extractor import detect_language, extract_text, extract_title
from .direct_ingest import ingest_page, ingest_pdf
from .pdf_parser import download_pdf, extract_text_from_pdf, detect_pdf_language

CBK_EXCLUDE = [
    "/login",
    "/portal",
    "/admin",
    "/search",
    "/print",
    "/redirects/",
    # These are aliases of /legislation-and-regulation/* and produce duplicate content
    "/supervision/cbk-regulations-and-instructions/",
    # Image-only pages with no extractable text
    "/organization/organization-chart",
    "/ar/about-cbk/committee-of-shariah/members",
]

# Banknote denomination pages (quarter/half/one/five/ten/twenty-kd-note) for
# issues 1-4 are image-only.  Fifth & sixth issues have real text so we keep them.
_IMAGE_ONLY_BANKNOTE_ISSUES = ("/first-issue/", "/second-issue/", "/third-issue/", "/fourth-issue/")

MIN_TEXT_LENGTH = 50  # Lower threshold so thin-but-real pages pass
MAX_RETRIES = 2       # Retry on timeout
RETRY_TIMEOUT = 60000 # 60s on retry

CBK_ACCESS_TAGS = {
    "source": "cbk_website",
    "category": "external_regulator",
}

SEEN_HASHES: set = set()
PDF_URLS: set = set()


def _is_pdf(url: str) -> bool:
    return url.lower().rstrip("/").endswith(".pdf")


def _is_excluded(url: str) -> bool:
    lower = url.lower()
    # PDFs are collected separately, not excluded
    if _is_pdf(url):
        return False
    if any(pat in lower for pat in CBK_EXCLUDE):
        return True
    # Reject bare /ar and /en (redirect to homepage, duplicate content)
    path = urlparse(lower).path.rstrip("/")
    if path in ("/ar", "/en"):
        return True
    # Old banknote denomination pages (issues 1-4) are image-only
    if any(issue in lower for issue in _IMAGE_ONLY_BANKNOTE_ISSUES) and "-kd-note" in lower:
        return True
    return False


def _same_domain(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc in ("www.cbk.gov.kw", "cbk.gov.kw")


def run() -> dict:
    stats = {"site": "CBK", "urls_discovered": 0, "scraped": 0, "ingested": 0, "skipped": 0, "errors": 0, "pdfs_ingested": 0, "pdfs_failed": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(30000)

        # Discover links from the homepage navigation
        print("[CBK-PW] Loading homepage...")
        try:
            page.goto(CBK_BASE_URL, wait_until="networkidle", timeout=60000)
            time.sleep(3)
        except Exception as e:
            print(f"[CBK-PW] Failed to load homepage: {e}")
            browser.close()
            return stats

        # Grab all internal links from the rendered page
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
        )

        discovered = set()
        discovered.add(CBK_BASE_URL)
        for link in links:
            if _same_domain(link):
                clean = link.split("#")[0].split("?")[0].rstrip("/")
                if not clean:
                    continue
                if _is_pdf(clean):
                    PDF_URLS.add(clean)
                elif not _is_excluded(clean):
                    discovered.add(clean)

        # Only add known paths that are verified to return 200
        known_paths = [
            "/ar/about-cbk/welcome",
            "/ar/about-cbk/mission-and-objectives",
            "/ar/about-cbk/governor/profile",
            "/ar/about-cbk/deputy-governor-profile",
            "/ar/about-cbk/board-of-directors/members",
            "/ar/about-cbk/board-of-directors/responsibilities",
            "/ar/about-cbk/organization/directory",
            "/ar/supervision/basic-functions-and-tasks",
            "/ar/supervision/customer-protection-unit/introduction",
            "/ar/supervision/faq",
            "/ar/supervision/penalties",
            "/ar/legislation-and-regulation/cbk-law/Law-intro",
            "/ar/legislation-and-regulation/cbk-law/chapter-one",
            "/ar/legislation-and-regulation/cbk-law/chapter-two",
            "/ar/legislation-and-regulation/cbk-law/chapter-three",
            "/ar/legislation-and-regulation/cbk-law/chapter-four",
            "/ar/monetary-policy/monetary-policy-objectives",
            "/ar/monetary-policy/exchange-rate-policy",
            "/ar/payment-systems/payment-systems-intro",
            "/ar/statistics-and-publication/nsdp",
            "/ar/cbk-news/covid-19-measures",
            "/en/about-cbk/welcome",
            "/en/about-cbk/mission-and-objectives",
            "/en/about-cbk/governor/profile",
            "/en/about-cbk/deputy-governor-profile",
            "/en/about-cbk/board-of-directors/members",
            "/en/about-cbk/board-of-directors/responsibilities",
            "/en/about-cbk/organization/directory",
            "/en/about-cbk/offices-and-locations/offices",
            "/en/about-cbk/history/former-governors",
            "/en/about-cbk/history/former-deputy-governor",
            "/en/about-cbk/committee-of-shariah/members",
            "/en/about-cbk/committee-of-shariah/responsibilities",
            "/en/banknotes-and-coins/banknotes/fifth-issue",
        ]
        for path in known_paths:
            full = CBK_BASE_URL.rstrip("/") + path
            if not _is_excluded(full):
                discovered.add(full)

        urls = sorted(discovered)[:MAX_PAGES_PER_SITE]
        stats["urls_discovered"] = len(urls)
        print(f"[CBK-PW] Discovered {len(urls)} URLs")

        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")

            # --- Navigate with retry on timeout ---
            html = None
            resp = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    timeout = 30000 if attempt == 1 else RETRY_TIMEOUT
                    resp = page.goto(url, wait_until="networkidle", timeout=timeout)
                    time.sleep(2)
                    html = page.content()
                    break  # success
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

            if len(html) < 100:
                stats["errors"] += 1
                print("  [ERROR] Empty page")
                continue

            # --- Discover sub-links for BFS depth-2 ---
            try:
                sub_links = page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
                )
                for link in sub_links:
                    if _same_domain(link):
                        clean = link.split("#")[0].split("?")[0].rstrip("/")
                        if not clean:
                            continue
                        if _is_pdf(clean):
                            PDF_URLS.add(clean)
                        elif not _is_excluded(clean) and clean not in discovered and len(urls) < MAX_PAGES_PER_SITE:
                            discovered.add(clean)
                            urls.append(clean)
            except Exception:
                pass

            # --- Extract & ingest ---
            text = extract_text(html)
            if not text or len(text) < MIN_TEXT_LENGTH:
                stats["errors"] += 1
                print(f"  [ERROR] Too little content ({len(text) if text else 0} chars)")
                continue

            content_hash = hashlib.sha256(text.encode()).hexdigest()
            if content_hash in SEEN_HASHES:
                # Still ingest under this URL as an alias so the citation works
                title = extract_title(html)[:80] if extract_title(html) else url.split("/")[-1]
                lang = detect_language(text)
                result = ingest_page(
                    text=text,
                    title=title,
                    source_uri=url,
                    language=lang,
                    doc_type="web_page",
                    access_tags=CBK_ACCESS_TAGS,
                )
                if result:
                    stats["ingested"] += 1
                    print(f"  [OK] {title[:60]} ({lang}) - {result.get('chunks_ingested', 0)} chunks [alias]")
                else:
                    stats["errors"] += 1
                    print("  [ERROR] Ingestion returned None")
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
                access_tags=CBK_ACCESS_TAGS,
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
        print(f"\n[CBK-PW] Discovered {len(PDF_URLS)} PDF URLs. Downloading and parsing...")
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
            # Use filename as title
            filename = pdf_url.split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ")
            total_chars = sum(len(p["text"]) for p in pages)
            print(f"    {len(pages)} pages, {total_chars} chars, lang={lang}")

            result = ingest_pdf(
                pages=pages,
                title=filename,
                source_uri=pdf_url,
                language=lang,
                doc_type="pdf",
                access_tags=CBK_ACCESS_TAGS,
            )
            if result:
                stats["pdfs_ingested"] += 1
                print(f"    [OK] {result['chunks_ingested']} chunks from {result['pages']} pages")
            else:
                stats["pdfs_failed"] += 1
                print(f"    [ERROR] Ingestion returned None")

            time.sleep(0.5)

    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("Central Bank of Kuwait Website Scraper (Playwright)")
    print("=" * 60)
    result = run()
    print("\n" + "=" * 60)
    print("CBK Scrape Summary:")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("=" * 60)
