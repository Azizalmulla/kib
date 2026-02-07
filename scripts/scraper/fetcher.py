"""HTTP fetching with retries, rate limiting, and robots.txt compliance."""

import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from .config import (
    MAX_RETRIES,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT,
    USER_AGENT,
)

_robots_cache: dict[str, RobotFileParser] = {}
_last_request_time: dict[str, float] = {}


def _get_robots_parser(base_url: str) -> RobotFileParser:
    if base_url in _robots_cache:
        return _robots_cache[base_url]

    rp = RobotFileParser()
    robots_url = f"{base_url}/robots.txt"
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(robots_url, headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.allow_all = True
    except Exception:
        rp.allow_all = True

    _robots_cache[base_url] = rp
    return rp


def is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    rp = _get_robots_parser(base)
    return rp.can_fetch(USER_AGENT, url)


def _rate_limit(domain: str) -> None:
    last = _last_request_time.get(domain, 0)
    elapsed = time.time() - last
    if elapsed < REQUEST_DELAY_SECONDS:
        time.sleep(REQUEST_DELAY_SECONDS - elapsed)
    _last_request_time[domain] = time.time()


def fetch_html(url: str) -> Optional[str]:
    if not is_allowed(url):
        print(f"  [BLOCKED by robots.txt] {url}")
        return None

    domain = urlparse(url).netloc
    _rate_limit(domain)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            ) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code in (403, 404, 410):
                    return None
                print(f"  [HTTP {resp.status_code}] {url} (attempt {attempt})")
        except Exception as exc:
            print(f"  [ERROR] {url} attempt {attempt}: {exc}")
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)

    return None
