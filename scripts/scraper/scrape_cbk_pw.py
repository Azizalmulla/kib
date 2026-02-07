"""Scrape Central Bank of Kuwait website using Playwright (JS-rendered SPA)."""

import hashlib
import sys
import time
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

from .config import CBK_BASE_URL, REQUEST_DELAY_SECONDS, MAX_PAGES_PER_SITE
from .extractor import detect_language, extract_text, extract_title
from .direct_ingest import ingest_page as db_ingest

CBK_EXCLUDE = [
    "/login",
    "/portal",
    "/admin",
    "/search",
    "/print",
]

CBK_ACCESS_TAGS = {
    "source": "cbk_website",
    "category": "external_regulator",
}

SEEN_HASHES: set = set()


def _is_excluded(url: str) -> bool:
    lower = url.lower()
    return any(pat in lower for pat in CBK_EXCLUDE)


def _same_domain(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc in ("www.cbk.gov.kw", "cbk.gov.kw")


def run() -> dict:
    stats = {"site": "CBK", "urls_discovered": 0, "scraped": 0, "ingested": 0, "skipped": 0, "errors": 0}

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
            if _same_domain(link) and not _is_excluded(link):
                clean = link.split("#")[0].split("?")[0].rstrip("/")
                if clean:
                    discovered.add(clean)

        # Also try known CBK sections
        known_paths = [
            "/en/about-cbk/overview",
            "/en/about-cbk/board-of-directors",
            "/en/about-cbk/governor-word",
            "/en/supervision/banking-regulation",
            "/en/supervision/licensed-banks",
            "/en/consumer-protection",
            "/en/consumer-protection/complaints",
            "/en/anti-money-laundering",
            "/en/statistics-and-publication",
            "/en/laws-and-regulations",
            "/en/monetary-policy",
            "/en/exchange-rates",
            "/en/financial-stability",
            "/ar/about-cbk/overview",
            "/ar/supervision/banking-regulation",
            "/ar/consumer-protection",
            "/ar/anti-money-laundering",
            "/ar/laws-and-regulations",
            "/ar/monetary-policy",
            "/ar/exchange-rates",
        ]
        for path in known_paths:
            discovered.add(CBK_BASE_URL.rstrip("/") + path)

        urls = sorted(discovered)[:MAX_PAGES_PER_SITE]
        stats["urls_discovered"] = len(urls)
        print(f"[CBK-PW] Discovered {len(urls)} URLs")

        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")
            try:
                resp = page.goto(url, wait_until="networkidle", timeout=30000)
                if resp and resp.status >= 400:
                    print(f"  [SKIP] HTTP {resp.status}")
                    stats["skipped"] += 1
                    continue
                time.sleep(2)

                html = page.content()
                if not html or len(html) < 200:
                    print("  [SKIP] Empty page")
                    stats["skipped"] += 1
                    continue

                # Also discover links from this page for BFS depth-2
                try:
                    sub_links = page.eval_on_selector_all(
                        "a[href]",
                        "els => els.map(e => e.href).filter(h => h.startsWith('http'))"
                    )
                    for link in sub_links:
                        if _same_domain(link) and not _is_excluded(link):
                            clean = link.split("#")[0].split("?")[0].rstrip("/")
                            if clean and clean not in discovered and len(urls) < MAX_PAGES_PER_SITE:
                                discovered.add(clean)
                                urls.append(clean)
                except Exception:
                    pass

                text = extract_text(html)
                if not text or len(text) < 200:
                    print("  [SKIP] Too little content")
                    stats["skipped"] += 1
                    continue

                content_hash = hashlib.sha256(text.encode()).hexdigest()
                if content_hash in SEEN_HASHES:
                    print("  [SKIP] Duplicate content")
                    stats["skipped"] += 1
                    continue
                SEEN_HASHES.add(content_hash)

                title = extract_title(html)[:80] if extract_title(html) else url.split("/")[-1]
                lang = detect_language(text)
                stats["scraped"] += 1

                result = db_ingest(
                    text=text,
                    title=title,
                    source_uri=url,
                    language=lang,
                    doc_type="web_page",
                    access_tags=CBK_ACCESS_TAGS,
                )

                if result:
                    stats["ingested"] += 1
                    print(f"  [OK] {title[:60]} ({lang}) - {result['chunks_ingested']} chunks")
                else:
                    stats["skipped"] += 1
                    print("  [SKIP] Ingestion returned None")

            except Exception as e:
                stats["errors"] += 1
                print(f"  [ERROR] {e}")

            time.sleep(REQUEST_DELAY_SECONDS)

        browser.close()

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
