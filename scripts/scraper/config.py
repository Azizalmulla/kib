"""Scraper configuration."""

import os

# Ingestion service URL
INGEST_URL = os.getenv("KIB_INGEST_URL", "http://localhost:8001/ingest")

# Rate limiting
REQUEST_DELAY_SECONDS = float(os.getenv("SCRAPER_DELAY", "1.5"))
REQUEST_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "30"))
MAX_RETRIES = 3

# Crawl limits
MAX_PAGES_PER_SITE = int(os.getenv("SCRAPER_MAX_PAGES", "200"))
BFS_MAX_DEPTH = 3

# Content filters
MIN_TEXT_LENGTH = 200

# User agent
USER_AGENT = "KIB-Knowledge-Copilot-Scraper/1.0 (+https://github.com/Azizalmulla/kib)"

# KIB
KIB_BASE_URL = "https://www.kib.com.kw"
KIB_SITEMAP_URL = "https://www.kib.com.kw/sitemap.xml"

# CBK
CBK_BASE_URL = "https://www.cbk.gov.kw"
CBK_SITEMAP_URL = "https://www.cbk.gov.kw/sitemap.xml"

# URL patterns to exclude (login, media, duplicates, etc.)
EXCLUDE_PATTERNS = [
    "/login",
    "/signin",
    "/signup",
    "/register",
    "/auth/",
    "/admin/",
    "/wp-admin/",
    "/wp-login",
    "/cart",
    "/checkout",
    "/my-account",
    "/search",
    "/feed",
    "/rss",
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".mp4",
    ".mp3",
    ".zip",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    "#",
    "javascript:",
    "mailto:",
    "tel:",
]
