"""
Comprehensive crawler for KIB + CBK websites.

Crawls all HTML pages and PDFs from both sites using Playwright,
including targeted section crawlers for CBK regulation/instruction PDFs.
Generates crawl_report.json as an audit trail.

Run from project root:
    python -m scripts.scraper.crawl_all
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright, Page, Browser

import psycopg

from .config import REQUEST_DELAY_SECONDS, MAX_PAGES_PER_SITE
from .extractor import detect_language, extract_text, extract_title
from .direct_ingest import ingest_page, ingest_pdf, DB_URL
from .pdf_parser import download_pdf, download_pdf_playwright, extract_text_from_pdf, detect_pdf_language


def _already_ingested(source_uri: str) -> bool:
    """Check if a URL has already been ingested into the DB."""
    try:
        with psycopg.connect(DB_URL) as conn:
            row = conn.execute(
                "SELECT 1 FROM document_versions WHERE source_uri = %s LIMIT 1",
                (source_uri,),
            ).fetchone()
            return row is not None
    except Exception:
        return False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KIB_BASE = "https://www.kib.com.kw"
CBK_BASE = "https://www.cbk.gov.kw"

KIB_ACCESS_TAGS = {"source": "kib_website", "type": "internal_site", "tags": ["public", "internal_site", "kib"]}
CBK_ACCESS_TAGS = {"source": "cbk_website", "category": "external_regulator"}

KIB_EXCLUDE = ["/online-banking", "/ebanking", "/login", "/apply", "/portal", "/ONPAY", "/cgi-bin"]
CBK_EXCLUDE = ["/login", "/portal", "/admin", "/search", "/print", "/redirects/",
               "/supervision/cbk-regulations-and-instructions/"]

# CBK pages with known dense PDF listings (EN + AR)
CBK_PDF_SECTIONS = [
    "/en/supervision/cbk-regulations-and-instructions/instructions-for-conventional-banks",
    "/en/supervision/cbk-regulations-and-instructions/instructions-for-islamic-banks",
    "/en/supervision/cbk-regulations-and-instructions/instructions-for-finance-companies",
    "/en/supervision/cbk-regulations-and-instructions/instructions-for-investment-companies",
    "/en/supervision/cbk-regulations-and-instructions/instructions-for-exchange-companies",
    "/en/statistics-and-publication/annual-publications/economic-reports",
    "/en/statistics-and-publication/annual-publications/financial-stability-report",
    "/en/legislation-and-regulation/cbk-law/Law-intro",
    "/ar/supervision/cbk-regulations-and-instructions/instructions-for-conventional-banks",
    "/ar/supervision/cbk-regulations-and-instructions/instructions-for-islamic-banks",
    "/ar/supervision/cbk-regulations-and-instructions/instructions-for-finance-companies",
    "/ar/supervision/cbk-regulations-and-instructions/instructions-for-investment-companies",
    "/ar/supervision/cbk-regulations-and-instructions/instructions-for-exchange-companies",
    "/ar/statistics-and-publication/annual-publications/economic-reports",
    "/ar/statistics-and-publication/annual-publications/financial-stability-report",
    "/ar/legislation-and-regulation/cbk-law/Law-intro",
]

# Image-only banknote pages
_IMAGE_ONLY_BANKNOTE = ("/first-issue/", "/second-issue/", "/third-issue/", "/fourth-issue/")

MIN_TEXT = 50
MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class CrawlReport:
    """Accumulates stats and writes crawl_report.json."""

    def __init__(self):
        self.started = datetime.now(timezone.utc).isoformat()
        self.html_pages: list = []
        self.pdfs: list = []
        self.errors: list = []
        self.stats = {
            "kib_html": 0, "kib_pdfs": 0,
            "cbk_html": 0, "cbk_pdfs": 0,
            "total_chunks": 0, "total_errors": 0,
        }

    def add_html(self, site: str, url: str, title: str, lang: str, chunks: int):
        self.html_pages.append({"site": site, "url": url, "title": title, "lang": lang, "chunks": chunks})
        self.stats[f"{site}_html"] += 1
        self.stats["total_chunks"] += chunks

    def add_pdf(self, site: str, url: str, title: str, lang: str, pages: int, chunks: int, ocr: bool = False):
        self.pdfs.append({"site": site, "url": url, "title": title, "lang": lang, "pages": pages, "chunks": chunks, "ocr": ocr})
        self.stats[f"{site}_pdfs"] += 1
        self.stats["total_chunks"] += chunks

    def add_error(self, site: str, url: str, error: str):
        self.errors.append({"site": site, "url": url, "error": error})
        self.stats["total_errors"] += 1

    def save(self, path: str = "crawl_report.json"):
        report = {
            "started": self.started,
            "finished": datetime.now(timezone.utc).isoformat(),
            "stats": self.stats,
            "html_pages": self.html_pages,
            "pdfs": self.pdfs,
            "errors": self.errors,
        }
        Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\n[REPORT] Saved to {path}")


def _is_pdf(url: str) -> bool:
    return url.lower().split("?")[0].split("#")[0].rstrip("/").endswith(".pdf")


def _clean(url: str) -> str:
    return url.split("#")[0].split("?")[0].rstrip("/")


def _same_domain(url: str, domain: str) -> bool:
    netloc = urlparse(url).netloc.lower()
    return domain in netloc


def _is_excluded(url: str, excludes: list) -> bool:
    lower = url.lower()
    return any(pat in lower for pat in excludes)


def _collect_links(page: Page, domain: str, excludes: list,
                   discovered: set, html_urls: list, pdf_urls: set):
    """Extract links from the current page, classify as HTML or PDF."""
    try:
        links = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
        )
    except Exception:
        return

    for link in links:
        if not _same_domain(link, domain):
            continue
        clean = _clean(link)
        if not clean:
            continue
        if _is_pdf(clean) or _is_pdf(link):
            pdf_urls.add(clean if _is_pdf(clean) else link)
        elif clean not in discovered and not _is_excluded(clean, excludes):
            discovered.add(clean)
            html_urls.append(clean)

    # Also collect /dam/ links on KIB (JCR assets that may be PDFs)
    if "kib.com.kw" in domain:
        try:
            dam_links = page.eval_on_selector_all(
                "a[href*='/dam/']",
                "els => els.map(e => e.href)"
            )
            for dl in dam_links:
                if _is_pdf(dl):
                    pdf_urls.add(_clean(dl))
        except Exception:
            pass

    # Collect /redirects/download links on CBK (direct downloads)
    if "cbk.gov.kw" in domain:
        try:
            dl_links = page.eval_on_selector_all(
                "a[href*='redirects/download'], a[href*='redirect/download']",
                "els => els.map(e => e.href)"
            )
            for dl in dl_links:
                pdf_urls.add(dl)
        except Exception:
            pass


def _navigate(page: Page, url: str) -> Optional[str]:
    """Navigate to URL with retry. Returns HTML or None."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            timeout = 30000 if attempt == 1 else 60000
            resp = page.goto(url, wait_until="networkidle", timeout=timeout)
            time.sleep(2)
            if resp and resp.status >= 400:
                return None
            return page.content()
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(3)
            else:
                print(f"  [NAV-ERR] {e}")
    return None


# ---------------------------------------------------------------------------
# Site crawlers
# ---------------------------------------------------------------------------

def crawl_site(
    browser: Browser,
    base_url: str,
    domain: str,
    site_tag: str,
    excludes: list,
    access_tags: dict,
    known_paths: list,
    report: CrawlReport,
):
    """Crawl a site: discover pages via Playwright BFS, collect + ingest HTML & PDFs."""
    page = browser.new_page()
    page.set_default_timeout(30000)

    discovered: set = set()
    html_urls: list = []
    pdf_urls: set = set()
    seen_hashes: set = set()

    # 1. Load homepage and discover links
    print(f"\n{'='*60}")
    print(f"[{site_tag.upper()}] Crawling {base_url}")
    print(f"{'='*60}")

    html = _navigate(page, base_url)
    if html:
        discovered.add(_clean(base_url))
        html_urls.append(_clean(base_url))
        _collect_links(page, domain, excludes, discovered, html_urls, pdf_urls)

    # 2. Add known paths
    for path in known_paths:
        full = base_url.rstrip("/") + path
        clean = _clean(full)
        if clean not in discovered and not _is_excluded(clean, excludes):
            discovered.add(clean)
            html_urls.append(clean)

    print(f"[{site_tag.upper()}] Initial: {len(html_urls)} HTML URLs + {len(pdf_urls)} PDFs")

    # 3. BFS crawl — visit each page, discover more links
    i = 0
    while i < len(html_urls) and i < MAX_PAGES_PER_SITE:
        url = html_urls[i]
        i += 1
        print(f"[{i}/{len(html_urls)}] {url}")

        html = _navigate(page, url)
        if not html:
            report.add_error(site_tag, url, "navigation failed")
            continue

        # Discover more links from this page
        _collect_links(page, domain, excludes, discovered, html_urls, pdf_urls)

        # Extract text
        text = extract_text(html)
        if not text or len(text) < MIN_TEXT:
            print(f"  [SKIP] Too little content ({len(text) if text else 0} chars)")
            continue

        content_hash = hashlib.sha256(text.encode()).hexdigest()
        if content_hash in seen_hashes:
            print("  [SKIP] Duplicate")
            continue
        seen_hashes.add(content_hash)

        if _already_ingested(url):
            print("  [SKIP] Already in DB")
            continue

        title = (extract_title(html) or url.split("/")[-1])[:80]
        lang = detect_language(text)

        result = ingest_page(
            text=text, title=title, source_uri=url, language=lang,
            doc_type="web_page", access_tags=access_tags,
        )
        if result:
            chunks = result.get("chunks_ingested", 0)
            report.add_html(site_tag, url, title, lang, chunks)
            print(f"  [OK] {title[:60]} ({lang}) - {chunks} chunks")
        else:
            report.add_error(site_tag, url, "ingest returned None")

        time.sleep(REQUEST_DELAY_SECONDS)

    page.close()
    return pdf_urls


def crawl_cbk_pdf_sections(browser: Browser, pdf_urls: set):
    """Visit CBK's known PDF-heavy section pages to discover all PDF links."""
    page = browser.new_page()
    page.set_default_timeout(30000)

    print(f"\n[CBK-SECTIONS] Scanning {len(CBK_PDF_SECTIONS)} regulation/publication pages...")
    for section_path in CBK_PDF_SECTIONS:
        url = CBK_BASE + section_path
        print(f"  {section_path}")
        html = _navigate(page, url)
        if not html:
            continue

        try:
            links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href).filter(h => h.includes('.pdf') || h.includes('download') || h.includes('redirect'))"
            )
            before = len(pdf_urls)
            for link in links:
                pdf_urls.add(_clean(link) if _is_pdf(link) else link)
            after = len(pdf_urls)
            print(f"    +{after - before} new PDFs (total: {after})")
        except Exception:
            pass

        time.sleep(0.5)

    page.close()


def ingest_all_pdfs(pdf_urls: set, site_tag: str, access_tags: dict, report: CrawlReport,
                    browser: Optional[Browser] = None):
    """Download, parse, and ingest all discovered PDFs."""
    if not pdf_urls:
        return

    # Open a Playwright page for downloads if browser provided
    pw_page = browser.new_page() if browser else None

    print(f"\n[{site_tag.upper()}-PDF] Ingesting {len(pdf_urls)} PDFs...")
    seen_hashes: set = set()

    for j, pdf_url in enumerate(sorted(pdf_urls), 1):
        print(f"  [{j}/{len(pdf_urls)}] {pdf_url[:100]}")

        if _already_ingested(pdf_url):
            print("    [SKIP] Already in DB")
            continue

        # Try requests first (faster), fall back to Playwright for SSL issues
        pdf_bytes = download_pdf(pdf_url)
        if not pdf_bytes and pw_page:
            pdf_bytes = download_pdf_playwright(pdf_url, pw_page)
        if not pdf_bytes:
            report.add_error(site_tag, pdf_url, "download failed")
            continue

        # Dedup by content hash
        content_hash = hashlib.sha256(pdf_bytes).hexdigest()
        if content_hash in seen_hashes:
            print(f"    [SKIP] Duplicate PDF content")
            continue
        seen_hashes.add(content_hash)

        pages = extract_text_from_pdf(pdf_bytes)
        if not pages:
            report.add_error(site_tag, pdf_url, "no extractable text")
            continue

        lang = detect_pdf_language(pages)
        filename = pdf_url.split("/")[-1].split("?")[0]
        title = filename.replace(".pdf", "").replace("-", " ").replace("_", " ").replace("%20", " ")[:80]
        total_chars = sum(len(p["text"]) for p in pages)
        print(f"    {len(pages)} pages, {total_chars} chars, lang={lang}")

        result = ingest_pdf(
            pages=pages, title=title, source_uri=pdf_url,
            language=lang, doc_type="pdf", access_tags=access_tags,
        )
        if result:
            report.add_pdf(site_tag, pdf_url, title, lang, result["pages"], result["chunks_ingested"])
            print(f"    [OK] {result['chunks_ingested']} chunks")
        else:
            report.add_error(site_tag, pdf_url, "ingest returned None")

        time.sleep(0.3)

    if pw_page:
        pw_page.close()


# ---------------------------------------------------------------------------
# Known paths
# ---------------------------------------------------------------------------

KIB_KNOWN_PATHS = [
    "/home/Personal.html",
    "/home/Personal/Accounts.html",
    "/home/Personal/Cards.html",
    "/home/Personal/Financing.html",
    "/home/Personal/Deposits.html",
    "/home/Personal/Digital-Banking.html",
    "/home/Personal/Takaful.html",
    "/home/Business.html",
    "/home/Business/Accounts.html",
    "/home/Business/Financing.html",
    "/home/Business/Trade-Finance.html",
    "/home/Business/Treasury.html",
    "/home/Real-Estate.html",
    "/home/Personal/Accounts/Savings-Account.html",
    "/home/Personal/Accounts/Current-Account.html",
    "/home/Personal/Accounts/Al-Boushra-Account.html",
    "/home/Personal/Financing/Personal-Financing.html",
    "/home/Personal/Financing/Auto-Financing.html",
    "/home/Personal/Financing/Real-Estate-Financing.html",
    "/home/Personal/Cards/Credit-Cards.html",
    "/home/Personal/Cards/Debit-Cards.html",
    "/home/Personal/Cards/Prepaid-Cards.html",
    "/home/Personal/Deposits/Term-Deposit.html",
    "/home/Personal/Digital-Banking/KIB-Mobile.html",
    "/home/Personal/Digital-Banking/KIB-Online.html",
]

CBK_KNOWN_PATHS = [
    "/en/about-cbk/welcome",
    "/en/about-cbk/mission-and-objectives",
    "/en/about-cbk/governor/profile",
    "/en/about-cbk/deputy-governor-profile",
    "/en/about-cbk/board-of-directors/members",
    "/en/about-cbk/board-of-directors/responsibilities",
    "/en/about-cbk/organization/directory",
    "/en/supervision/basic-functions-and-tasks",
    "/en/supervision/customer-protection-unit/introduction",
    "/en/supervision/faq",
    "/en/supervision/penalties",
    "/en/legislation-and-regulation/cbk-law/Law-intro",
    "/en/legislation-and-regulation/cbk-law/chapter-one",
    "/en/legislation-and-regulation/cbk-law/chapter-two",
    "/en/legislation-and-regulation/cbk-law/chapter-three",
    "/en/legislation-and-regulation/cbk-law/chapter-four",
    "/en/monetary-policy/monetary-policy-objectives",
    "/en/monetary-policy/exchange-rate-policy",
    "/en/payment-systems/payment-systems-intro",
    "/en/statistics-and-publication/nsdp",
    "/en/cbk-news/covid-19-measures",
    "/ar/about-cbk/welcome",
    "/ar/about-cbk/mission-and-objectives",
    "/ar/about-cbk/governor/profile",
    "/ar/about-cbk/deputy-governor-profile",
    "/ar/about-cbk/board-of-directors/members",
    "/ar/about-cbk/board-of-directors/responsibilities",
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
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    report = CrawlReport()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---- KIB ----
        kib_pdfs = crawl_site(
            browser, KIB_BASE, "kib.com.kw", "kib",
            KIB_EXCLUDE, KIB_ACCESS_TAGS, KIB_KNOWN_PATHS, report,
        )
        ingest_all_pdfs(kib_pdfs, "kib", KIB_ACCESS_TAGS, report, browser)

        # ---- CBK ----
        cbk_pdfs = crawl_site(
            browser, CBK_BASE, "cbk.gov.kw", "cbk",
            CBK_EXCLUDE, CBK_ACCESS_TAGS, CBK_KNOWN_PATHS, report,
        )
        # Targeted section crawl for PDF-heavy regulation pages
        crawl_cbk_pdf_sections(browser, cbk_pdfs)
        ingest_all_pdfs(cbk_pdfs, "cbk", CBK_ACCESS_TAGS, report, browser)

        browser.close()

    # ---- Report ----
    report.save("crawl_report.json")

    print("\n" + "=" * 60)
    print("CRAWL COMPLETE")
    print("=" * 60)
    for k, v in report.stats.items():
        print(f"  {k}: {v}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
