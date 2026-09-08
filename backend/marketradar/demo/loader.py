"""Loads the fictional demo universe: industries, companies and knowledge-graph edges.

Kept separate from ingestion because this is *reference* data, not evidence. The loader can
only write ``DataMode.DEMO``; database check constraints reject a demo company that is not
fictional and a demo document whose URL could resolve, so a mislabelled row cannot be
persisted even if this code were wrong.

Knowledge-graph edges are curated here rather than fetched from a provider because no
supply-chain provider is implemented in this build. That is a stated limitation, not a
hidden one: every seeded edge carries ``data_mode = DEMO`` and a note describing the link.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, EntityType, RelationshipType
from marketradar.domain.models import Company, EntityRelationship, Industry, Security
from marketradar.errors import ValidationError
from marketradar.logging import get_logger
from marketradar.providers.fixture import FixtureCompanyProvider

log = get_logger(__name__)


@dataclass
class SeedReport:
    industries: int = 0
    companies: int = 0
    securities: int = 0
    relationships: int = 0


def seed_demo_universe(
    session: Session, provider: FixtureCompanyProvider | None = None
) -> SeedReport:
    """Idempotently load the demo reference universe."""
    provider = provider or FixtureCompanyProvider()
    report = SeedReport()

    health = provider.health()
    if health.mode != DataMode.DEMO:
        raise ValidationError(
            "The demo loader refuses to run against a non-DEMO provider",
            provider=provider.key,
            mode=health.mode.value,
        )

    # --- industries (parents first so the self-reference resolves) ------
    by_key: dict[str, Industry] = {
        row.key: row for row in session.scalars(select(Industry)).all()
    }
    pending = list(provider.industries())
    for _ in range(len(pending) + 1):
        if not pending:
            break
        deferred = []
        for raw in pending:
            parent_key = raw.get("parent")
            if parent_key and parent_key not in by_key:
                deferred.append(raw)
                continue
            industry_row = by_key.get(raw["key"])
            if industry_row is None:
                industry_row = Industry(
                    key=raw["key"],
                    name=raw["name"],
                    sector=raw.get("sector"),
                    parent_id=by_key[parent_key].id if parent_key else None,
                    data_mode=DataMode.DEMO,
                )
                session.add(industry_row)
                session.flush()
                report.industries += 1
            by_key[raw["key"]] = industry_row
        pending = deferred
    if pending:  # pragma: no cover - corpus integrity error
        raise ValidationError(
            "Industry hierarchy in the corpus is not resolvable",
            unresolved=[p["key"] for p in pending],
        )

    # --- companies + securities ----------------------------------------
    companies = {row.key: row for row in session.scalars(select(Company)).all()}
    for descriptor in provider.list_companies():
        if not descriptor.is_fictional:
            # Belt and braces alongside the database constraint: the demo universe must
            # never contain a real issuer (ADR-006).
            raise ValidationError(
                "Demo corpus contains a non-fictional company", company=descriptor.key
            )
        company_row = companies.get(descriptor.key)
        if company_row is None:
            industry = by_key.get(descriptor.industry_key or "")
            company_row = Company(
                key=descriptor.key,
                name=descriptor.name,
                ticker=descriptor.ticker,
                exchange=descriptor.exchange,
                industry_id=industry.id if industry is not None else None,
                description=descriptor.description,
                is_fictional=True,
                data_mode=DataMode.DEMO,
                provenance_note=(
                    "Fictional issuer from the synthetic development corpus. Not a real "
                    "company and not a tradeable security."
                ),
            )
            session.add(company_row)
            session.flush()
            companies[descriptor.key] = company_row
            report.companies += 1

            if descriptor.ticker and descriptor.exchange:
                existing_security = session.scalar(
                    select(Security).where(
                        Security.ticker == descriptor.ticker,
                        Security.exchange == descriptor.exchange,
                    )
                )
                if existing_security is None:
                    session.add(
                        Security(
                            company_id=company_row.id,
                            ticker=descriptor.ticker,
                            exchange=descriptor.exchange,
                            data_mode=DataMode.DEMO,
                        )
                    )
                    report.securities += 1

    # --- knowledge graph edges -----------------------------------------
    existing_edges = {
        (
            edge.source_entity_type.value,
            edge.source_entity_key,
            edge.target_entity_type.value,
            edge.target_entity_key,
            edge.relationship_type.value,
        )
        for edge in session.scalars(select(EntityRelationship)).all()
    }
    for raw in provider.relationships():
        key = (
            raw["source_entity_type"],
            raw["source_entity_key"],
            raw["target_entity_type"],
            raw["target_entity_key"],
            raw["relationship_type"],
        )
        if key in existing_edges:
            continue
        session.add(
            EntityRelationship(
                source_entity_type=EntityType(raw["source_entity_type"]),
                source_entity_key=raw["source_entity_key"],
                target_entity_type=EntityType(raw["target_entity_type"]),
                target_entity_key=raw["target_entity_key"],
                relationship_type=RelationshipType(raw["relationship_type"]),
                weight=raw["weight"],
                confidence=raw["confidence"],
                note=raw.get("note"),
                data_mode=DataMode.DEMO,
            )
        )
        report.relationships += 1

    session.flush()
    log.info(
        "demo.seeded",
        industries=report.industries,
        companies=report.companies,
        relationships=report.relationships,
    )
    return report
