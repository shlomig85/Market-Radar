"""Company reference-data synchronisation.

Turns whatever the configured ``CompanyDataProvider`` knows into ``Company`` rows. Kept
separate from document ingestion because reference data has a different lifecycle: it
changes slowly, is not evidence, and carries no ancestry.

The database constraint does the real enforcement — a ``DEMO`` company must be fictional
and a non-``DEMO`` company must not be — so a provider that mislabels its output fails
loudly at insert rather than quietly polluting the universe.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode
from marketradar.domain.models import Company, Security
from marketradar.logging import get_logger
from marketradar.providers.base import ProviderCompany

log = get_logger(__name__)


@dataclass
class CompanySyncReport:
    seen: int = 0
    created: int = 0
    updated: int = 0
    securities_created: int = 0
    mode: DataMode = DataMode.UNAVAILABLE


def sync_companies(
    session: Session, companies: tuple[ProviderCompany, ...], mode: DataMode
) -> CompanySyncReport:
    """Insert or update company rows. Idempotent."""
    report = CompanySyncReport(seen=len(companies), mode=mode)
    if not companies:
        return report

    existing = {row.key: row for row in session.scalars(select(Company)).all()}
    listings = {
        (row.ticker, row.exchange) for row in session.scalars(select(Security)).all()
    }

    for descriptor in companies:
        row = existing.get(descriptor.key)
        if row is None:
            row = Company(
                key=descriptor.key,
                name=descriptor.name,
                ticker=descriptor.ticker,
                exchange=descriptor.exchange,
                cik=descriptor.cik,
                country=descriptor.country,
                description=descriptor.description,
                is_fictional=descriptor.is_fictional,
                data_mode=descriptor.data_mode,
                provenance_note=f"Synced from provider in {descriptor.data_mode.value} mode.",
            )
            session.add(row)
            report.created += 1
        else:
            # Names and listings change; the key (CIK-derived for SEC) does not.
            row.name = descriptor.name
            row.ticker = descriptor.ticker or row.ticker
            row.exchange = descriptor.exchange or row.exchange
            row.cik = descriptor.cik or row.cik
            report.updated += 1

        if descriptor.ticker and descriptor.exchange:
            listing = (descriptor.ticker, descriptor.exchange)
            if listing not in listings:
                session.flush()
                session.add(
                    Security(
                        company_id=row.id,
                        ticker=descriptor.ticker,
                        exchange=descriptor.exchange,
                        data_mode=descriptor.data_mode,
                    )
                )
                listings.add(listing)
                report.securities_created += 1

    session.flush()
    log.info(
        "companies.synced",
        mode=mode.value,
        created=report.created,
        updated=report.updated,
    )
    return report
