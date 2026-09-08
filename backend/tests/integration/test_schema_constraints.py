"""Database constraints: the guardrails that make mislabelled data unrepresentable."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from marketradar.domain.enums import DataMode, SourceClass, SourceType
from marketradar.domain.models import Company, Source, SourceDocument

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 8, tzinfo=UTC)


def _source(session, mode=DataMode.DEMO, synthetic=True, key="s-test"):
    source = Source(
        key=key,
        name="Test source",
        publisher="Test publisher",
        source_type=SourceType.MAJOR_FINANCIAL_MEDIA,
        source_class=SourceClass.FINANCIAL_MEDIA,
        base_quality=80,
        is_synthetic=synthetic,
        data_mode=mode,
    )
    session.add(source)
    session.flush()
    return source


def _document(source, url, mode=DataMode.DEMO, digest="a" * 64):
    return SourceDocument(
        source_id=source.id,
        url=url,
        title="t",
        body_text="body",
        content_hash=digest,
        published_at=NOW,
        event_at=NOW,
        retrieved_at=NOW,
        ingested_at=NOW,
        data_mode=mode,
        provider_key="test",
    )


def test_a_demo_document_must_use_a_non_resolvable_domain(session):
    """A synthetic row must never be able to look like a real citation."""
    source = _source(session)
    session.add(_document(source, "https://real-news-site.com/article"))
    with pytest.raises(IntegrityError, match="demo_documents_use_invalid_domain"):
        session.flush()


def test_a_demo_document_on_an_invalid_domain_is_accepted(session):
    source = _source(session)
    session.add(_document(source, "https://wire.demo.invalid/nbmx/2026/08/story"))
    session.flush()


def test_a_demo_company_must_be_fictional(session):
    """Synthetic evidence about a real ticker is unrepresentable (ADR-006)."""
    session.add(
        Company(key="real", name="A Real Issuer", ticker="REAL", is_fictional=False,
                data_mode=DataMode.DEMO)
    )
    with pytest.raises(IntegrityError, match="demo_companies_are_fictional"):
        session.flush()


def test_a_live_company_must_not_be_fictional(session):
    session.add(
        Company(key="made-up", name="Invented Co", is_fictional=True, data_mode=DataMode.LIVE)
    )
    with pytest.raises(IntegrityError, match="demo_companies_are_fictional"):
        session.flush()


def test_a_synthetic_source_must_be_demo_mode(session):
    with pytest.raises(IntegrityError, match="synthetic_sources_are_demo"):
        _source(session, mode=DataMode.LIVE, synthetic=True, key="bad-source")


def test_the_same_document_cannot_be_ingested_twice_from_one_source(session):
    source = _source(session)
    session.add(_document(source, "https://a.demo.invalid/1"))
    session.flush()
    session.add(_document(source, "https://a.demo.invalid/2"))
    with pytest.raises(IntegrityError, match="uq_document_source_content"):
        session.flush()


def test_identical_text_from_two_sources_remains_two_documents(session):
    """Syndication must stay two documents so ancestry can fold them into one confirmation
    — global hash uniqueness would silently discard the evidence of amplification."""
    first = _source(session, key="origin")
    second = _source(session, key="syndicator")
    session.add(_document(first, "https://a.demo.invalid/1"))
    session.add(_document(second, "https://b.demo.invalid/1"))
    session.flush()


def test_source_quality_must_be_a_percentage(session):
    source = Source(
        key="bad-quality",
        name="n",
        publisher="p",
        source_type=SourceType.MAJOR_FINANCIAL_MEDIA,
        source_class=SourceClass.FINANCIAL_MEDIA,
        base_quality=140,
        is_synthetic=True,
        data_mode=DataMode.DEMO,
    )
    session.add(source)
    with pytest.raises(IntegrityError, match="quality_range"):
        session.flush()
