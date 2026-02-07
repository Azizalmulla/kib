"""URL discovery via sitemap.xml or BFS crawl."""

import re
import xml.etree.ElementTree as ET
from collections import deque
from typing import List, Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .config import BFS_MAX_DEPTH, EXCLUDE_PATTERNS, MAX_PAGES_PER_SITE
from .fetcher import fetch_html, is_allowed


def _is_excluded(url: str) -> bool:
    lower = url.lower()
    return any(pat in lower for pat in EXCLUDE_PATTERNS)


def _same_domain(url: str, base: str) -> bool:
    return urlparse(url).netloc == urlparse(base).netloc


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if clean.endswith("/") and len(parsed.path) > 1:
        clean = clean.rstrip("/")
    return clean


def discover_from_sitemap(sitemap_url: str, base_url: str) -> List[str]:
    print(f"[SITEMAP] Fetching {sitemap_url}")
    html = fetch_html(sitemap_url)
    if not html:
        print("[SITEMAP] Not found or blocked, falling back to BFS")
        return []

    urls: Set[str] = set()
    try:
        root = ET.fromstring(html)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        sitemap_refs = root.findall(".//sm:sitemap/sm:loc", ns)
        if sitemap_refs:
            for ref in sitemap_refs[:10]:
                sub_urls = discover_from_sitemap(ref.text.strip(), base_url)
                urls.update(sub_urls)

        for loc in root.findall(".//sm:url/sm:loc", ns):
            url = loc.text.strip() if loc.text else ""
            if url and _same_domain(url, base_url) and not _is_excluded(url):
                urls.add(_normalize_url(url))

    except ET.ParseError:
        print("[SITEMAP] Failed to parse XML")
        return []

    result = sorted(urls)[:MAX_PAGES_PER_SITE]
    print(f"[SITEMAP] Found {len(result)} URLs")
    return result


def discover_bfs(base_url: str) -> List[str]:
    print(f"[BFS] Starting crawl from {base_url} (depth={BFS_MAX_DEPTH}, max={MAX_PAGES_PER_SITE})")
    visited: Set[str] = set()
    queue: deque = deque()
    queue.append((base_url, 0))
    visited.add(_normalize_url(base_url))

    discovered: List[str] = []

    while queue and len(discovered) < MAX_PAGES_PER_SITE:
        url, depth = queue.popleft()

        if _is_excluded(url):
            continue
        if not is_allowed(url):
            continue

        discovered.append(url)

        if depth >= BFS_MAX_DEPTH:
            continue

        html = fetch_html(url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            full = _normalize_url(urljoin(url, href))
            if (
                full not in visited
                and _same_domain(full, base_url)
                and not _is_excluded(full)
            ):
                visited.add(full)
                queue.append((full, depth + 1))

    print(f"[BFS] Discovered {len(discovered)} URLs")
    return discovered


def discover_urls(sitemap_url: str, base_url: str) -> List[str]:
    urls = discover_from_sitemap(sitemap_url, base_url)
    if not urls:
        urls = discover_bfs(base_url)
    return urls
