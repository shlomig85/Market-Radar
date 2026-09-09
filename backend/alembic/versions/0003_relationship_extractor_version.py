"""record which extractor version produced a graph edge

Revision ID: 0003_edge_extractor
Revises: 0002_knowable_at
Create Date: 2026-09-09 19:20:00.000000+00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0003_edge_extractor'
down_revision: str | None = '0002_knowable_at'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Distinguishes an edge READ OUT of a document from one seeded by hand. Without it,
    # retracting the output of a superseded extractor cannot tell the two apart, and would
    # either strand a wrong edge forever or delete a curated one. NULL means "not produced
    # by an extractor", which is the correct reading of every edge that exists today.
    op.add_column(
        "entity_relationships",
        sa.Column("extractor_version", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_entity_rel_extractor_version",
        "entity_relationships",
        ["extractor_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_entity_rel_extractor_version", table_name="entity_relationships")
    op.drop_column("entity_relationships", "extractor_version")
