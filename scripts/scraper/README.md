# KIB Data Pipeline - Web Scraper

Scrapes public pages from KIB and Central Bank of Kuwait websites, extracts clean text, and ingests into the KIB Knowledge Copilot.

## Setup

```bash
pip install -r scripts/scraper/requirements.txt
```

## Usage

```bash
# Scrape both sites
python -m scripts.scraper all

# Scrape KIB only
python -m scripts.scraper kib

# Scrape CBK only
python -m scripts.scraper cbk
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KIB_INGEST_URL` | `http://localhost:8001/ingest` | Ingestion service endpoint |
| `SCRAPER_DELAY` | `1.5` | Seconds between requests (rate limit) |
| `SCRAPER_TIMEOUT` | `30` | HTTP request timeout |
| `SCRAPER_MAX_PAGES` | `200` | Max pages per site |

## URL Discovery Strategy

1. Try `sitemap.xml` first (stable, fast, complete)
2. Fall back to BFS crawl (depth=3, max 200 pages)

## Excluded URL Patterns

- Login/auth pages (`/login`, `/signin`, `/auth/`)
- Admin pages (`/admin/`, `/wp-admin/`)
- Binary files (`.pdf`, `.jpg`, `.zip`, etc.)
- Cart/checkout pages
- Search/feed pages
- KIB-specific: `/online-banking`, `/ebanking`, `/apply`
- CBK-specific: `/portal`, `/admin`

## Tool Choices

| Tool | Reason |
|------|--------|
| `httpx` | Stable HTTP with retries, timeouts, and redirect following |
| `trafilatura` | Best-in-class main content extraction from messy HTML |
| `BeautifulSoup` | Fallback for edge cases where trafilatura fails |
| `sitemap.xml` first | Reduces crawl complexity, avoids missing pages |
| `SHA256 hashing` | Deduplication and change detection |
| `robots.txt` | Responsible crawling compliance |
| Language detection | Correct bilingual handling and RTL UI support |

## Access Tags

- **KIB pages**: `["public", "internal_site", "kib"]`
- **CBK pages**: `["public", "external_regulator", "cbk"]`
