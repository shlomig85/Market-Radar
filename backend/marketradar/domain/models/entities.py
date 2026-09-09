"""Entities and the knowledge graph.

Companies, industries and securities are nodes; ``EntityRelationship`` is the typed,
evidence-bearing edge table that the value-chain engine traverses. The AI-memory value
chain is *data* in these tables, not a code path (ADR-006 / architecture §8).
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketradar.db.base import Base, TimestampMixin, UuidPkMixin
from marketradar.db.types import StrEnumText
from marketradar.domain.enums import DataMode, EntityType, RelationshipType


class Industry(UuidPkMixin, TimestampMixin, Base):
    """A node in the sector taxonomy (Technology > Semiconductors > Memory > HBM)."""

    __tablename__ = "industries"

    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(128))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("industries.id"))
    description: Mapped[str | None] = mapped_column(Text)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    parent: Mapped[Industry | None] = relationship(remote_side="Industry.id")
    companies: Mapped[list[Company]] = relationship(back_populates="industry")


class Company(UuidPkMixin, TimestampMixin, Base):
    """A company node.

    ``is_fictional`` is not decoration: the development corpus uses fictional issuers so
    that no fabricated statement is ever attached to a real, tradeable security (ADR-006).
    Real issuers enter through the company-data provider and carry ``is_fictional = false``.
    """

    __tablename__ = "companies"

    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(32))
    exchange: Mapped[str | None] = mapped_column(String(32))
    cik: Mapped[str | None] = mapped_column(String(16))
    country: Mapped[str | None] = mapped_column(String(8))
    industry_id: Mapped[str | None] = mapped_column(ForeignKey("industries.id"))
    description: Mapped[str | None] = mapped_column(Text)
    is_fictional: Mapped[bool] = mapped_column(nullable=False, default=False)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    provenance_note: Mapped[str | None] = mapped_column(Text)

    industry: Mapped[Industry | None] = relationship(back_populates="companies")
    securities: Mapped[list[Security]] = relationship(back_populates="company")

    __table_args__ = (
        # A fictional company must be DEMO, and a DEMO company must be fictional. This makes
        # "synthetic data about a real ticker" unrepresentable.
        CheckConstraint(
            "(is_fictional = false AND data_mode <> 'DEMO') "
            "OR (is_fictional = true AND data_mode = 'DEMO')",
            name="demo_companies_are_fictional",
        ),
        Index("ix_companies_ticker", "ticker"),
        Index("ix_companies_industry_id", "industry_id"),
    )


class Security(UuidPkMixin, TimestampMixin, Base):
    """A tradeable instrument issued by a company."""

    __tablename__ = "securities"

    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(32), nullable=False)
    security_type: Mapped[str] = mapped_column(String(32), nullable=False, default="COMMON")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    company: Mapped[Company] = relationship(back_populates="securities")

    __table_args__ = (UniqueConstraint("ticker", "exchange", name="uq_security_listing"),)


class EntityRelationship(UuidPkMixin, TimestampMixin, Base):
    """A typed, weighted, evidence-bearing edge in the knowledge graph.

    Entities are addressed as ``(entity_type, entity_key)`` rather than by foreign key so
    the graph can span companies, industries, technologies, products and themes without a
    join table per pair.
    """

    __tablename__ = "entity_relationships"

    source_entity_type: Mapped[EntityType] = mapped_column(
        StrEnumText(EntityType), nullable=False
    )
    source_entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_entity_type: Mapped[EntityType] = mapped_column(
        StrEnumText(EntityType), nullable=False
    )
    target_entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    relationship_type: Mapped[RelationshipType] = mapped_column(
        StrEnumText(RelationshipType), nullable=False
    )
    #: Strength of the economic linkage (0..1). Multiplied along a value-chain path.
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    #: How sure we are the relationship exists at all (0..1).
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_items.id"))
    #: Which extractor produced this edge, or NULL when it was seeded rather than read out
    #: of a document. This is what lets a superseded extractor's output be retracted without
    #: touching curated edges — see marketradar.ingestion.retraction.
    extractor_version: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    __table_args__ = (
        CheckConstraint("weight >= 0 AND weight <= 1", name="weight_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        UniqueConstraint(
            "source_entity_type",
            "source_entity_key",
            "target_entity_type",
            "target_entity_key",
            "relationship_type",
            name="uq_relationship_edge",
        ),
        Index("ix_entity_rel_extractor_version", "extractor_version"),
        Index("ix_entity_rel_source", "source_entity_type", "source_entity_key"),
        Index("ix_entity_rel_target", "target_entity_type", "target_entity_key"),
    )
