"""Intelligence layer: events, signals, trends, themes and company exposure.

This is where documents become *measured change*. Nothing here holds text that is not
derived from an evidence row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketradar.db.base import Base, TimestampMixin, UuidPkMixin
from marketradar.db.types import JsonDict, StrEnumText
from marketradar.domain.enums import (
    DataMode,
    Direction,
    EntityType,
    EventType,
    ExposureRole,
    MarketAwareness,
    SignalCategory,
    TrendMaturity,
)


class Event(UuidPkMixin, TimestampMixin, Base):
    """A structured occurrence in the world, extracted from evidence.

    Events are the atomic units of the intelligence system. An event belongs to at most one
    ``EvidenceCluster``: repeated reporting of the same announcement produces one event, not
    one per article.
    """

    __tablename__ = "events"

    event_type: Mapped[EventType] = mapped_column(StrEnumText(EventType), nullable=False)
    direction: Mapped[Direction] = mapped_column(StrEnumText(Direction), nullable=False)
    #: Normalised size of the change (0..1), not a currency amount.
    magnitude: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    entity_type: Mapped[EntityType] = mapped_column(StrEnumText(EntityType), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_label: Mapped[str] = mapped_column(String(256), nullable=False)
    subject_key: Mapped[str | None] = mapped_column(String(128))

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The earliest instant this event could have been known: the publication time of the
    #: FIRST supporting document. Every as-of read filters on this, which is what stops
    #: information published after T from entering an analysis performed as of T.
    #: ``occurred_at`` alone is not sufficient — an event can occur long before it is
    #: reported, and using it as the only filter admits future information.
    knowable_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    cluster_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_clusters.id"))
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    #: Stable identity for idempotent re-runs: (cluster, type, entity, day).
    dedupe_key: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)

    evidence_links: Mapped[list[EventEvidence]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("magnitude >= 0 AND magnitude <= 1", name="magnitude_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_events_occurred_at", "occurred_at"),
        Index("ix_events_knowable_at", "knowable_at"),
        Index("ix_events_event_type", "event_type"),
        Index("ix_events_subject_key", "subject_key"),
        Index("ix_events_entity", "entity_type", "entity_key"),
    )


class EventEvidence(Base):
    """Association: which evidence rows support an event."""

    __tablename__ = "event_evidence"

    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="CASCADE"), primary_key=True
    )

    event: Mapped[Event] = relationship(back_populates="evidence_links")


class Signal(UuidPkMixin, TimestampMixin, Base):
    """A named measurable phenomenon tracked over time (e.g. 'AI memory demand')."""

    __tablename__ = "signals"

    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[SignalCategory] = mapped_column(StrEnumText(SignalCategory), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: Event types that contribute to this signal, with their sign.
    event_types: Mapped[dict | None] = mapped_column(JsonDict)

    observations: Mapped[list[SignalObservation]] = relationship(back_populates="signal")

    __table_args__ = (Index("ix_signals_subject_key", "subject_key"),)


class SignalObservation(UuidPkMixin, TimestampMixin, Base):
    """The value of a signal over one time bucket.

    ``strength`` is independence-adjusted: it is driven by the number of *distinct evidence
    clusters*, not by the number of documents.
    """

    __tablename__ = "signal_observations"

    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    strength: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    net_direction: Mapped[Direction] = mapped_column(StrEnumText(Direction), nullable=False)

    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    independent_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    underlying_event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_diversity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    primary_source_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_source_quality: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[dict | None] = mapped_column(JsonDict)

    signal: Mapped[Signal] = relationship(back_populates="observations")

    __table_args__ = (
        CheckConstraint("strength >= 0 AND strength <= 100", name="strength_range"),
        UniqueConstraint(
            "signal_id", "bucket_start", "computation_version", name="uq_signal_bucket"
        ),
        Index("ix_signal_obs_bucket_start", "bucket_start"),
    )


class Trend(UuidPkMixin, TimestampMixin, Base):
    """Change in a signal measured against its own historical baseline.

    A trend is not "people are talking about X". It is the comparison of an observation
    window against a baseline window; a stable, widely-discussed topic scores a high
    ``observation_strength`` and an ``acceleration`` near zero.
    """

    __tablename__ = "trends"

    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"), nullable=False)
    theme_id: Mapped[str | None] = mapped_column(ForeignKey("themes.id"))

    observation_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    observation_strength: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_strength: Mapped[float] = mapped_column(Float, nullable=False)
    #: -100..100. Positive means the signal is running above its own baseline.
    acceleration: Mapped[float] = mapped_column(Float, nullable=False)
    frequency_change: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    independence_change: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    novelty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    direction: Mapped[Direction] = mapped_column(StrEnumText(Direction), nullable=False)

    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computation_version: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[dict | None] = mapped_column(JsonDict)

    signal: Mapped[Signal] = relationship()

    __table_args__ = (
        CheckConstraint("acceleration >= -100 AND acceleration <= 100", name="acceleration_range"),
        UniqueConstraint(
            "signal_id", "observation_end", "computation_version", name="uq_trend_window"
        ),
        Index("ix_trends_theme_id", "theme_id"),
    )


class Theme(UuidPkMixin, TimestampMixin, Base):
    """An emerging investment theme formed from related trends."""

    __tablename__ = "themes"

    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    #: Graph entity keys the theme is anchored to; the value-chain walk starts here.
    anchor_entities: Mapped[dict | None] = mapped_column(JsonDict)

    maturity: Mapped[TrendMaturity] = mapped_column(
        StrEnumText(TrendMaturity), nullable=False, default=TrendMaturity.INVISIBLE
    )
    market_awareness: Mapped[MarketAwareness] = mapped_column(
        StrEnumText(MarketAwareness), nullable=False, default=MarketAwareness.UNKNOWN
    )
    #: Provenance of the awareness estimate specifically: without market data this is DEMO
    #: or UNAVAILABLE, and the UI says so rather than implying a measured figure.
    market_awareness_mode: Mapped[DataMode] = mapped_column(
        StrEnumText(DataMode), nullable=False, default=DataMode.UNAVAILABLE
    )

    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    theme_signals: Mapped[list[ThemeSignal]] = relationship(
        back_populates="theme", cascade="all, delete-orphan"
    )
    exposures: Mapped[list[ThemeCompanyExposure]] = relationship(back_populates="theme")

    __table_args__ = (
        Index("ix_themes_maturity", "maturity"),
        Index("ix_themes_last_updated_at", "last_updated_at"),
    )


class ThemeSignal(Base):
    """Association: which signals constitute a theme, and how heavily they count."""

    __tablename__ = "theme_signals"

    theme_id: Mapped[str] = mapped_column(
        ForeignKey("themes.id", ondelete="CASCADE"), primary_key=True
    )
    signal_id: Mapped[str] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    theme: Mapped[Theme] = relationship(back_populates="theme_signals")
    signal: Mapped[Signal] = relationship()


class ThemeCompanyExposure(UuidPkMixin, TimestampMixin, Base):
    """How exposed a company is to a theme, and by which path through the value chain."""

    __tablename__ = "theme_company_exposures"

    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.id"), nullable=False)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    role: Mapped[ExposureRole] = mapped_column(StrEnumText(ExposureRole), nullable=False)
    #: 1 = direct, 2 = second-order, 3 = third-order.
    order_of_effect: Mapped[int] = mapped_column(Integer, nullable=False)
    exposure_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    #: The exact hops taken through the graph — this is the explanation, not a rationale string.
    path: Mapped[dict | None] = mapped_column(JsonDict)
    rationale: Mapped[str | None] = mapped_column(Text)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    computation_version: Mapped[str] = mapped_column(String(32), nullable=False)

    theme: Mapped[Theme] = relationship(back_populates="exposures")

    __table_args__ = (
        CheckConstraint("exposure_score >= 0 AND exposure_score <= 100", name="exposure_range"),
        CheckConstraint("order_of_effect >= 1 AND order_of_effect <= 5", name="order_range"),
        UniqueConstraint(
            "theme_id", "company_id", "computation_version", name="uq_theme_company_exposure"
        ),
        Index("ix_exposures_theme_id", "theme_id"),
    )
