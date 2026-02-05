# KIB Knowledge Copilot

Production-grade RAG stack for KIB policies, procedures, products, and compliance docs.

## Deployment plan

- Frontend: Vercel
- Backend services + Postgres: Render
- Secrets: Render environment variables only (no keys in repo)

## Environment variables

### API Gateway (`services/api`)

- `KIB_DATABASE_URL` = `postgresql://user:pass@host:5432/kib`
- `KIB_RAG_SERVICE_URL` = `http://rag-service:8002`
- `KIB_REQUEST_TIMEOUT_SECONDS` = `20`
- `KIB_MOCK_OIDC` = `false` (use `true` for local dev)
- `KIB_OIDC_ISSUER` = `https://login.microsoftonline.com/<tenant-id>/v2.0`
- `KIB_OIDC_AUDIENCE` = `<client-id>`
- `KIB_OIDC_JWKS_URL` = `https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys`
- `KIB_OIDC_ROLES_CLAIM` = `roles`
- `KIB_OIDC_USER_CLAIM` = `preferred_username`
- `KIB_OIDC_NAME_CLAIM` = `name`
- `KIB_OIDC_DEPARTMENT_CLAIM` = `department`
- `KIB_AUDIT_READ_ROLES` = `compliance,audit_admin`

### RAG Service (`services/rag`)

- `KIB_DATABASE_URL` = `postgresql://user:pass@host:5432/kib`
- `KIB_EMBEDDING_MODEL` = `intfloat/multilingual-e5-base`
- `KIB_LLM_PROVIDER` = `openai_compatible` (or `ollama` for local verification)
- `KIB_LLM_BASE_URL` = `http://localhost:11434` (Ollama) or `https://<private-endpoint>`
- `KIB_LLM_MODEL` = `qwen2.5:7b-instruct`
- `KIB_LLM_API_KEY` = `` (optional for private endpoints)
- `KIB_LLM_TIMEOUT_SECONDS` = `30`

### Ingestion Service (`services/ingestion`)

- `KIB_DATABASE_URL` = `postgresql://user:pass@host:5432/kib`
- `KIB_UPLOADS_DIR` = `/data/uploads`
- `KIB_CHUNK_SIZE` = `800`
- `KIB_CHUNK_OVERLAP` = `100`
- `KIB_EMBEDDING_MODEL` = `intfloat/multilingual-e5-base`

### Frontend (`apps/web`)

- `NEXT_PUBLIC_KIB_API_BASE_URL` = `https://<render-api-gateway-url>`
- `NEXT_PUBLIC_KIB_MOCK_USER` = `user@kib.local` (local dev only)
- `NEXT_PUBLIC_KIB_MOCK_ROLES` = `front_desk` (local dev only)

## Tests

Run the RAG tests from the repo root:

```
make test-rag
```

This is the CI-safe command (non-zero exit on failure):

```
make ci
```
