"""Investigation trace spans (OpenTelemetry spans persisted per investigation).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

from ai_sre.services.repository import SPANS_DDL

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in (s.strip() for s in SPANS_DDL.split(";")):
        if statement:
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS investigation_spans")
