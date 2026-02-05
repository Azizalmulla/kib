# KIB Knowledge Copilot - Deployment Guide

## Architecture Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Frontend      │────▶│   API Gateway   │────▶│   RAG Service   │
│   (Vercel)      │     │   (Render)      │     │   (Render)      │
└─────────────────┘     └────────┬────────┘     └────────┬────────┘
                                 │                       │
                                 ▼                       ▼
                        ┌─────────────────┐     ┌─────────────────┐
                        │   PostgreSQL    │◀────│   Ingestion     │
                        │   (Render)      │     │   (Render)      │
                        └─────────────────┘     └─────────────────┘
```

---

## Step 1: Deploy Backend on Render

### Option A: Blueprint Deployment (Recommended)

1. Push code to GitHub/GitLab
2. Go to [Render Dashboard](https://dashboard.render.com)
3. Click **New** → **Blueprint**
4. Connect your repository
5. Render will detect `render.yaml` and create all services

### Option B: Manual Deployment

Create services manually in the order below.

---

## Step 2: Configure Environment Variables on Render

### After Blueprint deployment, set these secrets:

#### kib-api (API Gateway)
| Variable | Value |
|----------|-------|
| `KIB_MOCK_OIDC` | `true` for testing, `false` for production |
| `KIB_OIDC_ISSUER` | `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| `KIB_OIDC_AUDIENCE` | `<your-azure-app-client-id>` |
| `KIB_OIDC_JWKS_URL` | `https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys` |

#### kib-rag (RAG Service)
| Variable | Value |
|----------|-------|
| `KIB_LLM_PROVIDER` | `openai_compatible` |
| `KIB_LLM_BASE_URL` | `https://api.fireworks.ai/inference/v1` |
| `KIB_LLM_MODEL` | `fireworks/kimi-k2p5` |
| `KIB_LLM_API_KEY` | Your Fireworks API key |

---

## Step 3: Initialize Database

The database schema is automatically applied via `preDeployCommand` on first deploy.

To manually apply or update:

```bash
# Connect to Render shell for kib-api
python scripts/init_db.py
```

Enable pgvector extension (if not auto-enabled):
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

---

## Step 4: Deploy Frontend on Vercel

1. Go to [Vercel Dashboard](https://vercel.com/dashboard)
2. Click **Add New** → **Project**
3. Import your repository
4. Set **Root Directory** to `apps/web`
5. Configure environment variables:

| Variable | Value |
|----------|-------|
| `NEXT_PUBLIC_KIB_API_BASE_URL` | `https://kib-api.onrender.com` (your Render API URL) |

For local development only:
| Variable | Value |
|----------|-------|
| `NEXT_PUBLIC_KIB_MOCK_USER` | `user@kib.local` |
| `NEXT_PUBLIC_KIB_MOCK_ROLES` | `front_desk` |

6. Click **Deploy**

---

## Step 5: Verify Deployment

### Health Checks
```bash
# API Gateway
curl https://kib-api.onrender.com/health

# RAG Service
curl https://kib-rag.onrender.com/health

# Ingestion Service
curl https://kib-ingestion.onrender.com/health
```

### Test Chat (with mock auth)
```bash
curl -X POST https://kib-api.onrender.com/chat \
  -H "Content-Type: application/json" \
  -H "X-Mock-User: test@kib.local" \
  -H "X-Mock-Roles: front_desk" \
  -d '{"question": "What savings accounts are available?", "language": "en"}'
```

---

## Step 6: Ingest Test Documents

```bash
curl -X POST https://kib-ingestion.onrender.com/ingest \
  -F "file=@test-policy.txt" \
  -F "title=Savings Policy" \
  -F "doc_type=policy" \
  -F "language=en" \
  -F "version=v1" \
  -F "status=approved" \
  -F "allowed_roles=front_desk,compliance"
```

---

## Production Checklist

- [ ] Set `KIB_MOCK_OIDC=false` on kib-api
- [ ] Configure Azure AD OIDC settings
- [ ] Set LLM API credentials on kib-rag
- [ ] Verify CORS settings if needed
- [ ] Set up custom domain (optional)
- [ ] Configure Render auto-scaling (optional)
- [ ] Set up monitoring/alerting

---

## Troubleshooting

### "RAG service unavailable"
- Check kib-rag logs in Render dashboard
- Verify `KIB_RAG_SERVICE_URL` is set correctly on kib-api

### "Invalid token"
- Check OIDC configuration
- Set `KIB_MOCK_OIDC=true` for testing

### Database connection errors
- Check `KIB_DATABASE_URL` is set from Render's postgres service
- Verify pgvector extension is enabled
