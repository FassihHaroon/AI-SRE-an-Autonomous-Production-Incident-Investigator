"""Incident knowledge base: ingestion (load → parse → metadata → chunk → embed → store) and
hybrid retrieval (pgvector semantic + PostgreSQL full-text, RRF fusion, metadata filters,
metadata-aware reranking). See docs/knowledge.md.
"""

EMBEDDING_DIM = 384

# DDL shared by the Alembic migration and the integration tests (which run it in a scratch
# schema). Placeholders: none; runs in the current search_path.
KNOWLEDGE_DDL = f"""
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id               text PRIMARY KEY,
    source           text NOT NULL UNIQUE,
    doc_type         text NOT NULL,
    title            text NOT NULL,
    services         text[] NOT NULL DEFAULT '{{}}',
    environment      text,
    incident_id      text,
    severity         text,
    category         text,
    tags             text[] NOT NULL DEFAULT '{{}}',
    doc_timestamp    timestamptz,
    supersedes       text,
    content_hash     text NOT NULL,
    embedding_model  text NOT NULL,
    metadata         jsonb NOT NULL DEFAULT '{{}}',
    ingested_at      timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_content_hash ON knowledge_documents (content_hash);
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_services ON knowledge_documents USING gin (services);
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_tags ON knowledge_documents USING gin (tags);
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_type ON knowledge_documents (doc_type);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id           bigserial PRIMARY KEY,
    document_id  text NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    chunk_index  integer NOT NULL,
    section      text NOT NULL,
    content      text NOT NULL,
    embedding    vector({EMBEDDING_DIM}) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (
                     to_tsvector('english', coalesce(section, '') || ' ' || content)) STORED,
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_tsv ON knowledge_chunks USING gin (tsv);
"""
