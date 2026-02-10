# KIB Knowledge Copilot

RAG-powered knowledge assistant for KIB (Kuwait International Bank) employees. Provides grounded, cited answers from approved KIB and CBK (Central Bank of Kuwait) documents.

## Architecture

```
User → Next.js Frontend → API Gateway (FastAPI) → RAG Service (FastAPI) → PostgreSQL + pgvector
                                                                          ↕
                                                                    Fireworks AI (Qwen3)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15, React, CSS |
| API Gateway | FastAPI, httpx |
| RAG Service | FastAPI, psycopg, pgvector |
| Database | PostgreSQL + pgvector |
| LLM | Qwen3 8B via Fireworks AI |
| Embeddings | Qwen3 Embedding 8B via Fireworks AI |
| Scraping | Python requests, BeautifulSoup, PyMuPDF |
| Auth | JWT (HS256) |

## Features

- **RAG pipeline** — vector similarity search with pgvector, grounded answers with citations
- **Bilingual** — auto-detects Arabic/English, responds in the same language
- **Role-based access** — Front Desk (concise answers) and Compliance (detailed answers)
- **JWT authentication** — login with email/password, role derived from token
- **Conversation memory** — last 6 turns sent to LLM for follow-up questions
- **Guardrails** — refuses off-topic questions, confidence scoring, citation verification
- **Audit logging** — every query, response, and retrieved chunks logged to `audit_logs` table
- **Chat UI** — ChatGPT-style interface with sidebar history, streaming typewriter effect

## Data Sources

- **713 KIB PDFs** — annual reports, product T&Cs, disclosures
- **676 CBK PDFs** — regulatory frameworks, compliance guidelines
- **KIB website** — public HTML pages
- **CBK website** — public HTML pages

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL with pgvector extension

### 2. Set up environment

```bash
# Create .env in project root (not committed to git)
FIREWORKS_API_KEY=your_key_here
KIB_FIREWORKS_API_KEY=your_key_here
KIB_LLM_API_KEY=your_key_here
```

### 3. Start services

```bash
# Load env vars
export $(cat .env | xargs)

# RAG Service (port 8001)
.venv/bin/python -m uvicorn app.main:app --port 8001 --reload
# (from services/rag/)

# API Gateway (port 8000)
.venv/bin/python -m uvicorn app.main:app --port 8000 --reload
# (from services/api/)

# Frontend (port 3000)
cd apps/web && npm run dev
```

### 4. Login

| Role | Email | Password |
|------|-------|----------|
| Front Desk | frontdesk@kib.com | frontdesk123 |
| Compliance | compliance@kib.com | compliance123 |

## Environment Variables

### API Gateway (`services/api`)

| Variable | Description |
|----------|-------------|
| `KIB_DATABASE_URL` | PostgreSQL connection string |
| `KIB_RAG_SERVICE_URL` | RAG service URL (default: `http://localhost:8001`) |
| `KIB_JWT_SECRET` | JWT signing secret |

### RAG Service (`services/rag`)

| Variable | Description |
|----------|-------------|
| `KIB_DATABASE_URL` | PostgreSQL connection string |
| `KIB_FIREWORKS_API_KEY` | Fireworks AI API key |
| `KIB_LLM_API_KEY` | Fireworks AI API key (for LLM) |
| `KIB_EMBEDDING_MODEL` | Embedding model (default: `qwen3-embedding-8b`) |
| `KIB_LLM_MODEL` | LLM model (default: `qwen3-8b`) |

## Project Structure

```
kib/
├── apps/web/              # Next.js frontend
├── services/api/          # FastAPI API gateway (auth, routing, audit)
├── services/rag/          # FastAPI RAG service (retrieval, LLM)
├── scripts/scraper/       # Web scrapers (KIB, CBK)
├── scripts/               # Utility scripts (ingestion, backfill)
├── db/                    # Database schema
└── .env                   # API keys (gitignored)
```
