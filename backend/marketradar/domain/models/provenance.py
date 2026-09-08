"""Provenance: sources, documents, evidence and information ancestry.

This is the foundation of the whole system. Every downstream artefact — event, signal,
trend, finding, report line — must be able to reach a row in this module and answer
"where did this come from?".
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
    ClusterMethod,
    DataMode,
    Direction,
    EventType,
    SourceClass,
    SourceType,
)


class Source(UuidPkMixin, TimestampMixin, Base):
    """A publisher or data origin, with its base credibility prior.

    ``base_quality`` is a starting prior taken from the PRD's source table, not an
    immutable truth: it is a column so that observed reliability can update it later.
    """

    __tablename__ = "sources"

    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    publisher: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(StrEnumText(SourceType), nullable=False)
    source_class: Mapped[SourceClass] = mapped_column(StrEnumText(SourceClass), nullable=False)
    base_quality: Mapped[int] = mapped_column(Integer, nullable=False)
    homepage_url: Mapped[str | None] = mapped_column(String(512))
    #: True for sources that exist only to carry synthetic development data.
    is_synthetic: Mapped[bool] = mapped_column(nullable=False, default=False)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    documents: Mapped[list[SourceDocument]] = relationship(back_populates="source")

    __table_args__ = (
        CheckConstraint("base_quality >= 0 AND base_quality <= 100", name="quality_range"),
        CheckConstraint(
            "(is_synthetic = false) OR (data_mode = 'DEMO')",
            name="synthetic_sources_are_demo",
        ),
        Index("ix_sources_source_class", "source_class"),
    )


class EvidenceCluster(UuidPkMixin, TimestampMixin, Base):
    """A group of documents that repeat ONE underlying announcement.

    Ten articles rewriting a single press release form one cluster and therefore count as
    one independent confirmation, not ten. The cluster's ``origin_document_id`` is the
    earliest, highest-authority member.
    """

    __tablename__ = "evidence_clusters"

    cluster_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(512))
    origin_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.id", use_alter=True, name="fk_cluster_origin_document")
    )
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    documents: Mapped[list[SourceDocument]] = relationship(
        back_populates="cluster", foreign_keys="SourceDocument.cluster_id"
    )


class SourceDocument(UuidPkMixin, TimestampMixin, Base):
    """A retrieved document with full provenance.

    The three timestamps are deliberately separate columns (ADR-013):

    * ``published_at`` — when the document was published
    * ``event_at``     — when the thing it describes actually happened
    * ``retrieved_at`` — when we fetched it

    Recency is always computed from ``event_at``, so a 2026 article about a 2023 event
    cannot masquerade as a new signal.
    """

    __tablename__ = "source_documents"

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    author: Mapped[str | None] = mapped_column(String(256))
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: SHA-256 of the normalised body. Unique *per source*: re-ingesting the same document
    #: is a no-op, while identical text republished by a different outlet remains a distinct
    #: document that ancestry clustering will fold into one confirmation.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")

    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: True when ``event_at`` was inferred (usually from ``published_at``) rather than stated.
    event_at_inferred: Mapped[bool] = mapped_column(nullable=False, default=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_payload: Mapped[dict | None] = mapped_column(JsonDict)

    #: Explicit "this document reports that announcement" pointer, when the provider or the
    #: document itself declares one. Strongest ancestry evidence available.
    origin_ref: Mapped[str | None] = mapped_column(String(256))
    cluster_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_clusters.id"))
    cluster_method: Mapped[ClusterMethod | None] = mapped_column(StrEnumText(ClusterMethod))

    source: Mapped[Source] = relationship(back_populates="documents")
    cluster: Mapped[EvidenceCluster | None] = relationship(
        back_populates="documents", foreign_keys=[cluster_id]
    )
    evidence_items: Mapped[list[EvidenceItem]] = relationship(back_populates="document")

    __table_args__ = (
        # A DEMO document can never carry a resolvable URL: .invalid is reserved by RFC 2606.
        # This makes it structurally impossible for synthetic data to look like a real citation.
        CheckConstraint(
            "data_mode <> 'DEMO' OR url LIKE '%.invalid/%' OR url LIKE '%.invalid'",
            name="demo_documents_use_invalid_domain",
        ),
        UniqueConstraint("source_id", "content_hash", name="uq_document_source_content"),
        Index("ix_source_documents_content_hash", "content_hash"),
        Index("ix_source_documents_event_at", "event_at"),
        Index("ix_source_documents_published_at", "published_at"),
        Index("ix_source_documents_data_mode", "data_mode"),
        Index("ix_source_documents_cluster_id", "cluster_id"),
    )


class EvidenceItem(UuidPkMixin, TimestampMixin, Base):
    """A single claim-bearing excerpt extracted from exactly one document.

    This is the atom of the evidence ledger. A finding, a score or a report line references
    evidence rows; it never carries free-floating facts of its own.
    """

    __tablename__ = "evidence_items"

    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    #: Denormalised from the document so independence queries do not need a join.
    cluster_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_clusters.id"))

    claim: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excerpt_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    event_type: Mapped[EventType | None] = mapped_column(StrEnumText(EventType))
    direction: Mapped[Direction] = mapped_column(
        StrEnumText(Direction), nullable=False, default=Direction.NEUTRAL
    )
    magnitude: Mapped[float | None] = mapped_column(Float)
    entity_hint: Mapped[str | None] = mapped_column(String(256))
    subject_key: Mapped[str | None] = mapped_column(String(128))

    extracted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Which extraction rule produced this item. One sentence can legitimately carry several
    #: distinct claims ("demand is accelerating and capacity is committed"), so the rule is
    #: part of the evidence item's identity.
    rule_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # Denormalised provenance so an evidence row is self-describing in the API.
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    document: Mapped[SourceDocument] = relationship(back_populates="evidence_items")

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        UniqueConstraint(
            "document_id",
            "excerpt_start",
            "rule_key",
            "extractor_version",
            name="uq_evidence_span",
        ),
        Index("ix_evidence_items_event_at", "event_at"),
        Index("ix_evidence_items_event_type", "event_type"),
        Index("ix_evidence_items_cluster_id", "cluster_id"),
        Index("ix_evidence_items_subject_key", "subject_key"),
    )
