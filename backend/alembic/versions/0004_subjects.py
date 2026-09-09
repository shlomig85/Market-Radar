"""discovered subjects

Revision ID: 0004_subjects
Revises: 0003_edge_extractor
Create Date: 2026-09-09 20:10:00.000000+00:00
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = '0004_subjects'
down_revision: str | None = '0003_edge_extractor'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Subjects are DISCOVERED from the corpus rather than declared in code (audit C6), and
    # persist across runs so a topic keeps a stable key and a real first-seen date.
    op.create_table(
        "subjects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        # server_default matches every other table: TimestampMixin declares one, so without
        # it here the model and the migration disagree and every insert fails NOT NULL.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("term", sa.String(length=256), nullable=False),
        sa.Column("label", sa.String(length=256)),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cluster_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("emergence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("specificity", sa.Float(), nullable=False, server_default="0"),
        sa.Column("salience", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_discovered", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("discovery_version", sa.String(length=32), nullable=False),
        sa.Column("data_mode", sa.String(length=32), nullable=False),
        sa.CheckConstraint("cluster_count >= 0", name="cluster_count_non_negative"),
        sa.CheckConstraint("specificity >= 0 AND specificity <= 1", name="specificity_range"),
    )
    op.create_index("ix_subjects_salience", "subjects", ["salience"])
    op.create_index("ix_subjects_last_seen_at", "subjects", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_subjects_last_seen_at", table_name="subjects")
    op.drop_index("ix_subjects_salience", table_name="subjects")
    op.drop_table("subjects")
