"""Investigations, their event log (SSE replay) and application settings.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

from ai_sre.services.repository import INVESTIGATIONS_DDL

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in (s.strip() for s in INVESTIGATIONS_DDL.split(";")):
        if statement:
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS investigation_events")
    op.execute("DROP TABLE IF EXISTS investigations")
    op.execute("DROP TABLE IF EXISTS app_settings")
