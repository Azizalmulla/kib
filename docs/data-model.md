# KIB Knowledge Copilot - Data Model (Phase 1)

This model supports RBAC/ABAC at retrieval time, document governance, and auditability.

## Core entities

- users: Identity snapshot from OIDC (email, display_name, department, attributes)
- roles: Role catalog (e.g., front_desk, compliance)
- user_roles: User-to-role mapping
- documents: Canonical doc records with language, status, and access tags
- document_versions: Immutable versions with source_uri and checksum
- document_acl: Role-based access to documents (RBAC)
- chunks: Parsed chunk text with location anchors (page/section/offset)
- embeddings: pgvector embeddings per chunk
- audit_logs: Append-only trace of Q&A

## Governance gate

Only documents with status = approved are eligible for retrieval and answering. Draft or unapproved documents remain stored but are excluded from vector search.

## RBAC/ABAC

RBAC is enforced via document_acl (role_id). ABAC is supported by documents.access_tags and users.attributes for attribute-based policy evaluation. Retrieval services must filter by both before vector search.

## Embeddings + Vector search

Embeddings are stored in pgvector (one multilingual model for English/Arabic). The embeddings table is indexed with HNSW using cosine distance for fast similarity search.

## Citation anchors

Chunks include page_start/page_end and offset ranges. The UI uses document_versions.source_uri plus anchor metadata to link to the exact location.

## Audit logs

Every /chat request records the user, roles, question, retrieved chunk ids, answer, model/version, and timestamp.
