-- KIB Knowledge Copilot - initial schema
-- Requires extensions: pgcrypto for UUIDs, vector for embeddings

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE NOT NULL,
  display_name text,
  department text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text UNIQUE NOT NULL,
  description text
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title text NOT NULL,
  doc_type text,
  language text NOT NULL DEFAULT 'en',
  status text NOT NULL DEFAULT 'draft',
  access_tags jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS document_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  version text NOT NULL,
  source_uri text NOT NULL,
  sha256 text,
  page_count integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid REFERENCES users(id),
  is_active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS document_acl (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  role_id uuid NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  access_level text NOT NULL DEFAULT 'read'
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_document_acl ON document_acl(document_id, role_id);

CREATE TABLE IF NOT EXISTS chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_version_id uuid NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  chunk_index integer NOT NULL,
  text text NOT NULL,
  page_start integer,
  page_end integer,
  section text,
  offset_start integer,
  offset_end integer,
  hash text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Embedding dimension: 768 (qwen3-embedding:8b truncated via Matryoshka)
CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id uuid PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  embedding vector(768) NOT NULL,
  model text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES users(id),
  role_names text[] NOT NULL DEFAULT ARRAY[]::text[],
  query text NOT NULL,
  request_language text,
  response_language text,
  retrieved_chunk_ids uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
  answer text NOT NULL,
  model_provider text,
  model_name text,
  model_version text,
  retrieval_meta jsonb NOT NULL DEFAULT '{}'::jsonb,
  trace_id text,
  latency_ms integer,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feedback (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  audit_log_id uuid REFERENCES audit_logs(id) ON DELETE CASCADE,
  user_id uuid REFERENCES users(id),
  rating text NOT NULL CHECK (rating IN ('up', 'down')),
  correction text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_conversations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL,
  memory_snapshot text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
  role text NOT NULL CHECK (role IN ('user', 'assistant')),
  text text NOT NULL,
  response jsonb,
  audit_log_id uuid REFERENCES audit_logs(id) ON DELETE SET NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_memory (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  summary text NOT NULL DEFAULT '',
  preferred_language text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_audit_log ON feedback(audit_log_id);
CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating);
CREATE INDEX IF NOT EXISTS idx_chat_conversations_user_updated ON chat_conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation_created ON chat_messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_doc_acl_role ON document_acl(role_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_version ON chunks(document_version_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);

-- Vector index for similarity search (requires pgvector >= 0.5 for HNSW)
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
  ON embeddings
  USING hnsw (embedding vector_cosine_ops);
