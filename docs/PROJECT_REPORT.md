# KIB Knowledge Copilot — Project Report

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [System Architecture](#3-system-architecture)
4. [Data Collection & Ingestion](#4-data-collection--ingestion)
5. [RAG Pipeline](#5-rag-pipeline)
6. [Model Selection & Justification](#6-model-selection--justification)
7. [API Gateway, Authentication & Security](#7-api-gateway-authentication--security)
8. [Frontend & User Experience](#8-frontend--user-experience)
9. [Guardrails & Safety](#9-guardrails--safety)
10. [Technology Stack & Justification](#10-technology-stack--justification)
11. [Testing & Validation](#11-testing--validation)
12. [Limitations & Future Work](#12-limitations--future-work)

---

## 1. Executive Summary

The KIB Knowledge Copilot is a Retrieval-Augmented Generation (RAG) system built for Kuwait International Bank (KIB). It enables bank employees to ask natural language questions and receive grounded, cited answers drawn exclusively from approved KIB policies, product documentation, and Central Bank of Kuwait (CBK) regulations.

The system was designed with two primary user roles in mind: **front-desk employees** who need quick, concise answers for customer-facing interactions, and **compliance officers** who require detailed, auditable responses with exact regulatory references.

Key capabilities include:

- Bilingual support (Arabic and English) with automatic language detection
- Role-based access control with JWT authentication
- Grounded answers with source citations and confidence scoring
- Conversation memory for contextual follow-up questions
- Full audit trail of all user interactions

The system processes over 1,389 PDFs and hundreds of web pages from both KIB and CBK sources, making this knowledge instantly searchable through a ChatGPT-style interface.

---

## 2. Problem Statement

### 2.1 Context

KIB employees frequently need to reference bank policies, product terms and conditions, compliance guidelines, and CBK regulations. These documents are spread across multiple sources:

- The KIB public website (product pages, disclosures, annual reports)
- KIB published PDFs (policies, T&Cs, handbooks available on the website)
- The CBK website (regulatory circulars, compliance frameworks)
- CBK published PDFs (capital adequacy rules, AML guidelines, governance requirements)

### 2.2 Challenges

1. **Volume**: Over 1,389 PDFs across two institutions, many exceeding 100 pages
2. **Fragmentation**: Information is scattered across multiple websites and document formats
3. **Bilingual content**: Documents exist in both Arabic and English
4. **Accuracy requirements**: Banking and compliance answers must be precise and traceable to source documents
5. **Role sensitivity**: Different employee roles require different levels of detail and access

### 2.3 Objective

Build an AI-powered assistant that:

- Answers employee questions using only approved documents (no hallucination)
- Cites exact sources (document name, page number, relevant quote)
- Adapts response style to the user's role
- Supports both Arabic and English seamlessly
- Logs all interactions for compliance audit purposes

---

## 3. System Architecture

### 3.1 High-Level Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Frontend   │────▶│   API Gateway    │────▶│   RAG Service    │────▶│   PostgreSQL    │
│  (Next.js)   │     │   (FastAPI)      │     │   (FastAPI)      │     │   + pgvector    │
│  Port 3000   │     │   Port 8000      │     │   Port 8001      │     │                 │
└──────────────┘     └──────────────────┘     └──────────────────┘     └─────────────────┘
                              │                        │
                              │                        ▼
                              │               ┌─────────────────┐
                              │               │  Fireworks AI   │
                              │               │  (Qwen3 8B)     │
                              │               └─────────────────┘
                              ▼
                     ┌──────────────────┐
                     │   Audit Logs     │
                     │   (PostgreSQL)   │
                     └──────────────────┘
```

### 3.2 Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| **Frontend** | User interface, authentication flow, conversation management |
| **API Gateway** | Authentication, authorization, request routing, audit logging |
| **RAG Service** | Document retrieval, LLM interaction, response generation |
| **PostgreSQL + pgvector** | Document storage, vector embeddings, similarity search |
| **Fireworks AI** | LLM inference (Qwen3 8B) and embedding generation (Qwen3 Embedding 8B) |

### 3.3 Justification for Microservice Architecture

The system uses a three-tier architecture (frontend → API gateway → RAG service) rather than a monolithic design for several reasons:

1. **Separation of concerns**: Authentication/audit logic is decoupled from retrieval/generation logic
2. **Independent scaling**: The RAG service (compute-intensive) can be scaled independently from the API gateway
3. **Security boundary**: The RAG service is not directly exposed to the internet; all requests pass through the authenticated API gateway
4. **Maintainability**: Each service can be updated, tested, and deployed independently

---

## 4. Data Collection & Ingestion

### 4.1 Data Sources

| Source | Type | Count | Description |
|--------|------|-------|-------------|
| KIB Website | HTML pages | ~200+ pages | Product pages, T&Cs, policies, disclosures |
| KIB Documents | PDFs | 713 | Annual reports, product terms, financial disclosures |
| CBK Website | HTML pages | ~150+ pages | Regulations, circulars, instructions |
| CBK Documents | PDFs | 676 | Regulatory frameworks, compliance guidelines |
| **Total** | | **~1,389 PDFs + ~350 web pages** | |

### 4.2 Scraping Methodology

#### 4.2.1 HTML Scraping

We built custom Python crawlers for each website:

- **`scrape_kib.py`**: Crawls the KIB website starting from the homepage, discovers all internal links, and extracts page content
- **`scrape_cbk.py`**: Crawls the CBK website with the same approach

**Libraries used:**

| Library | Purpose | Justification |
|---------|---------|---------------|
| `requests` | HTTP requests | Lightweight, well-supported, sufficient for static HTML pages |
| `BeautifulSoup4` | HTML parsing | Industry-standard HTML parser, handles malformed HTML gracefully |

The crawlers perform breadth-first traversal of each website, following internal links while respecting domain boundaries. Each page's text content is extracted, cleaned, and stored.

#### 4.2.2 PDF Extraction

PDFs are processed using **PyMuPDF** (`fitz`):

| Library | Purpose | Justification |
|---------|---------|---------------|
| `PyMuPDF` | PDF text extraction | Fast, reliable, preserves page boundaries, handles Arabic text correctly |

**Why PyMuPDF over alternatives:**

- **vs. PyPDF2**: PyMuPDF is significantly faster and handles complex PDF layouts better
- **vs. pdfplumber**: PyMuPDF has better Arabic text extraction and lower memory usage
- **vs. Tika**: No Java dependency required, simpler deployment

Each PDF is processed page by page, preserving page numbers for accurate citation.

#### 4.2.3 Resumable Crawling

The scraping system supports resumable crawling through `crawl_continue.py`:

- Progress is tracked in `crawl_report.json`
- If a crawl is interrupted (network failure, timeout), it resumes from where it stopped
- Already-processed URLs and PDFs are skipped on retry

**Justification**: With ~1,400 PDFs to download and process, network interruptions are inevitable. Resumable crawling avoids re-downloading already-processed documents.

### 4.3 Ingestion Pipeline

The ingestion pipeline (`direct_ingest.py`) transforms raw text into searchable embeddings:

```
Raw Text → Clean → Chunk → Deduplicate → Embed → Store in PostgreSQL
```

#### Step 1: Text Cleaning

- Remove NUL bytes (`\x00`) that break PostgreSQL storage
- Normalize whitespace and line breaks
- Preserve meaningful formatting (headings, lists)

#### Step 2: Chunking

Text is split into chunks of approximately **800 characters** with **100-character overlap**.

**Justification for chunk size:**

- **Too small** (<300 characters): Loses context, fragments sentences
- **Too large** (>1500 characters): Reduces retrieval precision, wastes LLM context window
- **800 characters**: Balances context preservation with retrieval granularity
- **Overlap**: Ensures sentences split at chunk boundaries are still findable

Page boundaries are preserved so each chunk knows which page it came from (critical for citation accuracy).

#### Step 3: Deduplication

Each document is hashed using SHA-256. If the same document is ingested again (e.g., re-crawl), it is skipped.

**Justification**: Prevents duplicate chunks from inflating search results and wasting storage.

#### Step 4: Embedding Generation

Each chunk is converted to a **768-dimensional vector** using **Qwen3 Embedding 8B** via the Fireworks AI API.

#### Step 5: Storage

Chunks and their embeddings are stored in PostgreSQL with the **pgvector** extension.

### 4.4 Database Schema

```sql
documents          — title, doc_type, language, allowed_roles, status
document_versions  — version string, source URI, page count, SHA-256 hash
embeddings         — chunk text, vector (768-dim), page number, start/end offsets
users              — email, display name, department
audit_logs         — user_id, role, query, answer, retrieved chunks, latency
```

**Justification for PostgreSQL + pgvector:**

- **vs. Pinecone/Weaviate**: No additional service to manage, all data (documents, users, audit logs, vectors) in one database
- **vs. FAISS**: pgvector supports SQL filtering (e.g., filter by `allowed_roles`) alongside vector search, which FAISS cannot do
- **vs. ChromaDB**: pgvector is production-grade, supports concurrent access, and integrates with existing PostgreSQL tooling

---

## 5. RAG Pipeline

### 5.1 What is RAG?

Retrieval-Augmented Generation (RAG) is an approach that enhances LLM responses by first retrieving relevant documents from a knowledge base, then providing those documents as context to the LLM. This ensures answers are grounded in actual data rather than the model's training knowledge.

### 5.2 Query Processing Flow

```
1. User asks a question
2. Question is embedded (Qwen3 Embedding 8B → 768-dim vector)
3. Vector similarity search finds top-K matching chunks (pgvector cosine distance)
4. Chunks are filtered by user's role (document ACL check against roles table)
5. Only chunks from active, approved documents are included
6. System prompt + chunks + question + conversation history → Qwen3 8B
7. LLM generates structured JSON response
8. Citations are normalized (matched to actual chunk metadata)
9. Confidence is computed; guardrails check if answer is sufficient
10. Response returned to user with citations
```

### 5.3 Vector Similarity Search

We use pgvector's **cosine distance** operator (`<=>`) to find the most semantically similar chunks to the user's question.

**Why cosine distance:**

- Measures the angle between vectors, not magnitude — works well for text embeddings of varying lengths
- Industry standard for semantic search
- Efficient with pgvector's HNSW index (used in our schema)

**Top-K retrieval**: We retrieve the top 5 chunks by default. This provides enough context for a comprehensive answer without overwhelming the LLM's context window.

### 5.4 Prompt Engineering

The system prompt is dynamically constructed based on the user's role:

**Front Desk prompt**: Instructs the LLM to give concise answers (2-4 sentences) in simple language, suitable for quick customer-facing responses.

**Compliance prompt**: Instructs the LLM to give detailed answers with exact policy quotes, section numbers, and regulatory references.

Both prompts enforce:

- Answer ONLY from the provided chunks
- Never use training data or external knowledge
- Output structured JSON with: `answer`, `confidence`, `citations[]`, `missing_info`, `safe_next_steps`
- If the chunks are insufficient, refuse politely

### 5.5 Conversation Memory

The last **6 conversation turns** (user question + assistant answer pairs) are included in the prompt. This allows the LLM to understand follow-up questions like:

- "Tell me more about that"
- "What about in Arabic?"
- "And what does CBK say about this?"

**Justification for 6 turns**: Balances context richness with token budget. More than 6 turns risk exceeding the LLM context window when combined with retrieved chunks and system prompt.

### 5.6 Citation Normalization

After the LLM generates citations, they are matched back to actual chunk metadata in the database:

- Document ID → document title, version
- Page number → verified against the chunk's stored page number
- Quote → verified to exist in the chunk text
- Source URI → actual URL or file path

This prevents the LLM from fabricating citation details.

---

## 6. Model Selection & Justification

### 6.1 LLM: Qwen3 8B

| Criteria | Qwen3 8B | Evaluation |
|----------|----------|------------|
| **Arabic + English** | Native bilingual support | Critical for a Kuwaiti bank |
| **Thinking mode** | `<think>...</think>` reasoning before answering | Improves accuracy for grounded Q&A |
| **Structured output** | Reliably produces valid JSON | Required for citation extraction |
| **Size** | 8 billion parameters | Fast inference (5-15s per response) |
| **Hosting** | Fireworks AI (serverless) | No GPU infrastructure needed |

**Why not larger models (e.g., Qwen3 235B)?**

We tested Qwen3 235B but response times exceeded 30 seconds, making it unsuitable for interactive chat. The 8B model provides a good balance of quality and speed.

**Why not GPT-4 or Claude?**

- Qwen3 has stronger native Arabic support
- Fireworks AI pricing is more cost-effective for high-volume usage
- The thinking mode provides better reasoning for grounded answers
- Data residency considerations — Fireworks AI offers more deployment flexibility

### 6.2 Embedding Model: Qwen3 Embedding 8B

| Criteria | Qwen3 Embedding 8B | Evaluation |
|----------|---------------------|------------|
| **Dimensions** | 768 | Standard size, good balance of precision and storage |
| **Multilingual** | Arabic + English in same vector space | Critical for cross-lingual search |
| **Model family** | Same as LLM (Qwen3) | Consistent understanding between indexing and querying |
| **Hosting** | Fireworks AI | Same API, unified billing |

**Justification for same-family embedding and LLM:**

Using Qwen3 for both embedding and generation ensures semantic alignment — the way documents are indexed matches how queries are interpreted. This improves retrieval accuracy compared to mixing models from different families.

---

## 7. API Gateway, Authentication & Security

### 7.1 API Gateway

The API gateway (`services/api/`) is the single entry point for all client requests. It handles:

1. **Authentication**: Validates JWT tokens on every request
2. **Authorization**: Extracts user role from the token and passes it to the RAG service
3. **Request routing**: Forwards chat requests to the RAG service
4. **Audit logging**: Records every interaction in the `audit_logs` table

### 7.2 JWT Authentication

**How it works:**

```
1. User enters email + password on the login screen
2. POST /auth/login → backend validates credentials
3. Backend signs a JWT token (HS256) containing:
   - sub: user email
   - name: display name
   - roles: ["front_desk"] or ["compliance"]
   - department: user's department
   - exp: expiration timestamp (24 hours)
4. Token is returned to the frontend and stored in localStorage
5. Every subsequent API call includes: Authorization: Bearer <token>
6. API gateway decodes and validates the token on each request
```

**Justification for JWT over session-based auth:**

- **Stateless**: No server-side session storage needed
- **Scalable**: Works across multiple backend instances without session synchronization
- **Industry standard**: Well-understood, widely supported
- **Self-contained**: Role and user info are embedded in the token, reducing database lookups
- **Ready for SSO**: Can be replaced with OIDC tokens from enterprise identity providers (Azure AD, Okta) without changing the frontend

**Demo accounts** (hardcoded for demonstration):

| Role | Email | Password |
|------|-------|----------|
| Front Desk | frontdesk@kib.com | frontdesk123 |
| Compliance | compliance@kib.com | compliance123 |

In production, these would be replaced with KIB's corporate identity provider.

### 7.3 Role-Based Access Control (RBAC)

Access control operates at two levels:

**Level 1 — Document filtering**: Each document in the database is linked to permitted roles via a `document_acl` table joined with a `roles` table. During retrieval, chunks are filtered so users only see documents their role permits. For example, a compliance-only policy document would not appear in search results for a front-desk user.

**Level 2 — Response style**: The system prompt changes based on the user's role:

| Aspect | Front Desk | Compliance |
|--------|-----------|------------|
| Answer length | 2-4 sentences | Detailed paragraphs |
| Language level | Simple, no jargon | Technical, precise |
| Citations | Summary | Exact quotes with section numbers |
| Use case | Quick customer-facing answers | Audit-ready compliance responses |

### 7.4 Audit Logging

Every chat interaction is logged to the `audit_logs` table:

| Field | Description |
|-------|-------------|
| `user_id` | Who asked the question |
| `role_names` | Their role at the time |
| `query` | Exact question text |
| `answer` | Full answer text |
| `retrieved_chunk_ids` | Which document chunks were used |
| `request_language` | Detected language (en/ar) |
| `response_language` | Language of the response |
| `retrieval_meta` | Confidence level, missing info |
| `trace_id` | Unique request trace ID |
| `latency_ms` | Response time in milliseconds |
| `created_at` | Timestamp |

**Justification**: Banking compliance regulations require auditability. This log provides a complete trail of what employees asked, what answers they received, and which documents were cited — essential for regulatory review.

---

## 8. Frontend & User Experience

### 8.1 Technology Choice

| Choice | Justification |
|--------|---------------|
| **Next.js 14** | Modern React framework with server-side rendering, fast build times, built-in routing |
| **React** | Component-based architecture, large ecosystem, industry standard |
| **CSS (custom)** | Full control over design without framework overhead |

### 8.2 Features

#### Login Screen

- Clean, centered card design with KIB branding
- Email/password form with error handling
- JWT token stored in `localStorage` upon successful login
- Session persists across page refreshes until explicit logout or token expiry

#### Chat Interface

- **ChatGPT-style layout**: Sidebar for history, main area for conversation, right panel for sources
- **Message bubbles**: User messages on the right (green), assistant messages on the left (white)
- **Confidence badges**: Each answer shows its confidence level (high/medium/low)
- **Streaming typewriter effect**: Words appear one at a time using a JavaScript interval timer, providing visual feedback that the system is responding

#### Conversation History

- Conversations are saved to `localStorage` with unique IDs
- Previous conversations appear in the sidebar and can be reloaded
- Each conversation can be deleted individually
- "New conversation" button clears the current chat

#### Sources Panel

- Right-side panel showing all citations for the current answer
- Each citation displays: document title, version, page number, exact quote, and a link to the source document
- Clicking a message updates the sources panel to show that message's citations

#### Bilingual Support

- Language is auto-detected based on the presence of Arabic characters in the input
- Arabic responses are displayed right-to-left (RTL)
- The system responds in the same language as the question

---

## 9. Guardrails & Safety

### 9.1 Grounded Answers Only

The LLM is explicitly instructed in the system prompt to answer only from the provided document chunks. It must never use its own training data or general knowledge. This prevents hallucination — a critical requirement for banking applications where incorrect information could have legal consequences.

### 9.2 Confidence Scoring

Each answer receives a confidence rating based on the quality of retrieved chunks:

| Confidence | Criteria |
|-----------|----------|
| **High** | ≥ 2 citations AND average chunk similarity ≥ 0.70 |
| **Medium** | ≥ 1 citation AND average chunk similarity ≥ 0.55 |
| **Low** | Everything else |

The similarity score is computed as `1.0 - cosine_distance` between the query vector and each retrieved chunk vector.

### 9.3 Refusal Mechanism

If the system cannot find relevant chunks or confidence is too low, it returns a polite refusal message:

- English: "I can't answer from KIB's approved documents for this question."
- Arabic: "لا أستطيع الإجابة من مستندات KIB المعتمدة لهذا السؤال."

This is accompanied by suggested next steps (e.g., "Search by policy or product name", "Include the document section or clause title").

### 9.4 Off-Topic Rejection

Questions unrelated to KIB or CBK (e.g., "Who is the president?", "What's the weather?") are refused. The system only engages with questions that can be answered from the approved document corpus.

---

## 10. Technology Stack & Justification

| Component | Technology | Justification |
|-----------|-----------|---------------|
| **Frontend** | Next.js 14 (React) | Modern, fast, supports SSR, large ecosystem |
| **API Gateway** | FastAPI (Python) | Async support, automatic OpenAPI docs, type safety with Pydantic |
| **RAG Service** | FastAPI (Python) | Same framework as gateway for consistency, Python has best ML/NLP ecosystem |
| **Database** | PostgreSQL + pgvector (HNSW index) | Production-grade, combines relational data + vector search in one DB |
| **LLM** | Qwen3 8B (Fireworks AI) | Bilingual, thinking mode, structured output, cost-effective |
| **Embeddings** | Qwen3 Embedding 8B (Fireworks AI) | Same model family as LLM, strong multilingual support |
| **HTML Scraping** | requests + BeautifulSoup | Simple, reliable, sufficient for static sites |
| **PDF Extraction** | PyMuPDF (fitz) | Fast, accurate Arabic support, page-level extraction |
| **Authentication** | JWT (HS256) | Stateless, scalable, industry standard |
| **LLM Hosting** | Fireworks AI | Serverless inference, no GPU infrastructure needed, pay-per-token |

---

## 11. Testing & Validation

### 11.1 End-to-End Testing

The system was tested with various question types:

| Question Type | Expected Behavior | Result |
|--------------|-------------------|--------|
| KIB product question (English) | Grounded answer with citations | ✅ |
| CBK regulation question (English) | Grounded answer with citations | ✅ |
| Arabic question about KIB | Arabic answer with citations | ✅ |
| Off-topic question | Polite refusal | ✅ |
| Follow-up question | Contextual answer using conversation history | ✅ |
| Front desk role | Concise answer | ✅ |
| Compliance role | Detailed answer with exact quotes | ✅ |

### 11.2 Data Validation

- **713/713 KIB PDFs** successfully extracted and ingested
- **676/676 CBK PDFs** successfully extracted and ingested
- SHA-256 deduplication verified — no duplicate documents in the database
- Chunk page numbers verified against source PDFs for citation accuracy

---

## 12. Limitations & Future Work

### 12.1 Current Limitations

1. **No internal documents**: The system currently only contains publicly available KIB and CBK documents. Internal policies, SOPs, and handbooks require KIB to provide them.
2. **Demo authentication**: The current JWT system uses hardcoded demo accounts. Production deployment would integrate with KIB's corporate identity provider (e.g., Azure AD).
3. **No real-time document updates**: Documents are ingested via batch scripts. A production system would need automated re-crawling and ingestion when source documents change.
4. **Single-region deployment**: Currently runs locally. Production would require cloud deployment with appropriate data residency considerations.

### 12.2 Future Enhancements

1. **SSO integration**: Replace demo accounts with KIB's enterprise SSO (Azure AD / OIDC)
2. **Internal document ingestion**: Ingest KIB's private policies with proper role-based access tags
3. **Admin dashboard**: Web interface for viewing audit logs, managing documents, and monitoring system health
4. **Automated re-crawling**: Scheduled jobs to detect and ingest updated documents
5. **Advanced analytics**: Query pattern analysis to identify knowledge gaps and frequently asked questions
6. **Multi-modal support**: Handle documents with tables, charts, and images (OCR)

---

*Document prepared for academic review. All technical decisions are justified based on the project requirements, constraints, and industry best practices.*
