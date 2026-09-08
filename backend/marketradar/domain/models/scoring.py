"""Scores and their decomposition.

A score is never stored as a bare number. Every ``Score`` row has ``ScoreComponent``
children carrying the raw input, the normalised value, the configured weight, the effective
weight after renormalisation, the contribution and a human-readable explanation. That is
what makes "Opportunity 74" inspectable instead of oracular.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketradar.db.base import Base, TimestampMixin, UuidPkMixin
from marketradar.db.types import JsonDict, StrEnumText
from marketradar.domain.enums import DataMode


class Score(UuidPkMixin, TimestampMixin, Base):
    """A computed score for a subject, produced by a named, versioned score model."""

    __tablename__ = "scores"

    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Components that could not be computed because their inputs were UNAVAILABLE. The
    #: remaining weights were renormalised; this list is surfaced through the API (ADR-007).
    unavailable_components: Mapped[dict | None] = mapped_column(JsonDict)
    #: Total configured weight that was actually available. 1.0 means nothing was dropped.
    weight_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    components: Mapped[list[ScoreComponent]] = relationship(
        back_populates="score", cascade="all, delete-orphan", order_by="ScoreComponent.key"
    )

    __table_args__ = (
        CheckConstraint("value >= 0 AND value <= 100", name="value_range"),
        UniqueConstraint(
            "subject_type",
            "subject_id",
            "model_name",
            "model_version",
            "computed_at",
            name="uq_score_snapshot",
        ),
        Index("ix_scores_subject", "subject_type", "subject_id"),
    )


class ScoreComponent(UuidPkMixin, Base):
    """One weighted input to a score, with everything needed to reproduce it."""

    __tablename__ = "score_components"

    score_id: Mapped[str] = mapped_column(
        ForeignKey("scores.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    available: Mapped[bool] = mapped_column(nullable=False, default=True)
    raw_input: Mapped[float | None] = mapped_column(Float)
    normalized: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    effective_weight: Mapped[float] = mapped_column(Float, nullable=False)
    contribution: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    inputs: Mapped[dict | None] = mapped_column(JsonDict)

    score: Mapped[Score] = relationship(back_populates="components")

    __table_args__ = (
        UniqueConstraint("score_id", "key", name="uq_score_component"),
        Index("ix_score_components_score_id", "score_id"),
    )
