# KIB Knowledge Copilot - API Contracts (Phase 1)

Base URLs are service-local. The API gateway is intended as the public entry point.

## API Gateway (FastAPI)

### GET /auth/me
Returns the authenticated user profile and roles.

Response:
- user: { id, email, display_name, department, attributes }
- roles: [role_name]
- claims: { ... }

### POST /chat
Request:
- question: string
- language: string ("en" | "ar")
- top_k: integer (optional)

Response:
- language: "en" | "ar"
- answer: string
- confidence: "high" | "medium" | "low"
- citations: [ { doc_title, doc_id, document_version, page_number, start_offset, end_offset, quote, source_uri } ]
- missing_info: string | null
- safe_next_steps: [string]

### GET /documents
Query params:
- language (optional)
- q (optional)

Response:
- [{ id, title, doc_type, language, status }]

### GET /documents/{document_id}
Response:
- document: { id, title, doc_type, language, status }
- active_version: { id, version, source_uri, page_count }

### GET /audit
Query params:
- limit (optional, default 50)
- user_id (optional)

Response:
- [{ id, user_id, role_names, query, retrieved_chunk_ids, answer, model_name, model_version, created_at }]

## Ingestion Service (FastAPI)

### POST /ingest
Multipart form fields:
- file: file
- title: string
- doc_type: string
- language: string
- version: string
- status: string (e.g., "approved" or "draft")
- allowed_roles: comma-separated list (e.g., "front_desk,compliance")
- access_tags: JSON string (optional ABAC tags, e.g., {"department": "risk"})

Response:
- document_id
- document_version_id
- chunks_ingested

## RAG Service (FastAPI)

### POST /rag/answer
Request:
- question
- language
- top_k
- user: { id, role_names, attributes }

Response:
- language: "en" | "ar"
- answer: string
- confidence: "high" | "medium" | "low"
- citations: [ { doc_title, doc_id, document_version, page_number, start_offset, end_offset, quote, source_uri } ]
- missing_info: string | null
- safe_next_steps: [string]
