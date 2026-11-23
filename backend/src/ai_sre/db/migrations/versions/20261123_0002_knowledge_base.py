"""Knowledge base tables: documents and embedded chunks (pgvector HNSW + full-text GIN).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

from ai_sre.knowledge import KNOWLEDGE_DDL

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # asyncpg runs one statement per call.
    for statement in (s.strip() for s in KNOWLEDGE_DDL.split(";")):
        if statement:
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    op.execute("DROP TABLE IF EXISTS knowledge_documents")
