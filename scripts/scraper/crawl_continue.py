"""
Continue crawl: KIB PDFs + CBK (HTML + PDFs).
KIB HTML already done (162 docs in DB).

Run from project root:
    python -m scripts.scraper.crawl_continue
"""

import sys
import time

from playwright.sync_api import sync_playwright

from .crawl_all import (
    CBK_ACCESS_TAGS, CBK_BASE, CBK_EXCLUDE, CBK_KNOWN_PATHS,
    KIB_ACCESS_TAGS, KIB_BASE, KIB_EXCLUDE,
    CBK_PDF_SECTIONS,
    CrawlReport, crawl_site, crawl_cbk_pdf_sections, ingest_all_pdfs,
    _same_domain, _is_pdf, _clean, _is_excluded, _collect_links, _navigate,
    MAX_PAGES_PER_SITE,
)
from .extractor import detect_language, extract_text, extract_title
from .pdf_parser import download_pdf_playwright, extract_text_from_pdf, detect_pdf_language


def discover_kib_pdfs(browser) -> set:
    """Re-discover KIB PDF URLs by visiting key pages (no HTML re-ingestion)."""
    page = browser.new_page()
    page.set_default_timeout(30000)

    pdf_urls = set()

    print("[KIB-PDF-DISCOVERY] Scanning KIB pages for PDF links...")
    page.goto(KIB_BASE, wait_until="networkidle", timeout=60000)
    time.sleep(3)

    # Collect all links from homepage
    links = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
    )
    discovered = set()
    html_urls = [KIB_BASE]
    discovered.add(_clean(KIB_BASE))

    for link in links:
        if _same_domain(link, "kib.com.kw"):
            clean = _clean(link)
            if _is_pdf(clean) or _is_pdf(link):
                pdf_urls.add(clean if _is_pdf(clean) else link)
            elif clean not in discovered and not _is_excluded(clean, KIB_EXCLUDE):
                discovered.add(clean)
                html_urls.append(clean)

    # Quick BFS — visit pages just to find PDF links (don't re-ingest HTML)
    visited = 0
    while visited < len(html_urls) and visited < MAX_PAGES_PER_SITE:
        url = html_urls[visited]
        visited += 1

        if visited % 20 == 0:
            print(f"  [{visited}/{len(html_urls)}] scanning... ({len(pdf_urls)} PDFs found)")

        html = _navigate(page, url)
        if not html:
            continue

        _collect_links(page, "kib.com.kw", KIB_EXCLUDE, discovered, html_urls, pdf_urls)

    print(f"[KIB-PDF-DISCOVERY] Found {len(pdf_urls)} PDF URLs from {visited} pages")
    page.close()
    return pdf_urls


def main() -> int:
    report = CrawlReport()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # ---- CBK: HTML + PDFs ----
        cbk_pdfs = crawl_site(
            browser, CBK_BASE, "cbk.gov.kw", "cbk",
            CBK_EXCLUDE, CBK_ACCESS_TAGS, CBK_KNOWN_PATHS, report,
        )
        crawl_cbk_pdf_sections(browser, cbk_pdfs)
        ingest_all_pdfs(cbk_pdfs, "cbk", CBK_ACCESS_TAGS, report, browser)

        # ---- KIB PDFs (HTML already done, site may rate-limit) ----
        try:
            kib_pdfs = discover_kib_pdfs(browser)
            ingest_all_pdfs(kib_pdfs, "kib", KIB_ACCESS_TAGS, report, browser)
        except Exception as e:
            print(f"[KIB-PDF] Skipped due to error: {e}")

        browser.close()

    report.save("crawl_report.json")

    print("\n" + "=" * 60)
    print("CRAWL CONTINUE COMPLETE")
    print("=" * 60)
    for k, v in report.stats.items():
        print(f"  {k}: {v}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
