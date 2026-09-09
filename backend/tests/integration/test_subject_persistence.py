"""Persisting discovered subjects, and keeping them distinct from entities.

Discovery finds what a corpus is about; these tests cover what has to be true once that
becomes rows other stages depend on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, Direction, SourceClass, SourceType
from marketradar.domain.models import Company, Source, SourceDocument, Subject
from marketradar.evidence.extractor import SUBJECT_LEXICON
from marketradar.ingestion.subjects import (
    company_ngrams,
    refresh_subjects,
    subject_vocabulary,
)

AS_OF = datetime(2026, 9, 9, tzinfo=UTC)

#: Five *differently worded* reports about one topic. Identical bodies would be degenerate:
#: every phrase would then have identical support and the collocation rule could not tell a
#: topic from an incidental phrasing of it. Real corpora vary, and the fixture should too.
BODIES = (
    "Utilities are accelerating grid storage deployment across several regions.",
    "Grid storage procurement rose again as interconnection queues lengthened.",
    "Developers report that grid storage is outpacing installed manufacturing lines.",
    "Battery cell suppliers say grid storage orders are extending their lead times.",
    "Analysts note grid storage additions continue at a widening pace this period.",
)


@pytest.fixture
def corpus(session: Session) -> None:
    source = Source(
        key="wire",
        name="Wire",
        publisher="Wire",
        source_type=SourceType.MAJOR_FINANCIAL_MEDIA,
        source_class=SourceClass.FINANCIAL_MEDIA,
        base_quality=70,
        is_synthetic=False,
        data_mode=DataMode.LIVE,
    )
    session.add(source)
    session.add(
        Company(
            key="nbmx",
            name="Northbridge Memory Corp",
            is_fictional=False,
            data_mode=DataMode.LIVE,
        )
    )
    session.flush()
    for index, body in enumerate(BODIES):
        when = AS_OF - timedelta(days=index + 1)
        session.add(
            SourceDocument(
                source_id=source.id,
                url=f"https://wire.test/{index}",
                # Titles vary too: an identical headline on every document would give
                # its incidental phrasing the same support as the topic itself.
                title=f"Regional report {index}",
                body_text=body,
                content_hash=f"{index:064d}",
                word_count=40,
                published_at=when,
                event_at=when,
                retrieved_at=when,
                ingested_at=when,
                data_mode=DataMode.LIVE,
                provider_key="feeds",
                # Distinct clusters: five independent origins, not one story repeated.
                cluster_id=None,
            )
        )
    session.flush()


def test_a_discovered_subject_is_persisted_with_what_earned_it(
    session: Session, corpus: None
) -> None:
    report = refresh_subjects(session, as_of=AS_OF)
    assert report.created

    subject = session.scalar(select(Subject).where(Subject.key == "grid_storage"))
    assert subject is not None
    assert subject.is_discovered is True
    assert subject.cluster_count >= 3
    assert subject.term == "grid storage"


def test_lexicon_subjects_are_never_claimed_as_discoveries(
    session: Session, corpus: None
) -> None:
    """The system must not claim to have found a topic that was typed into it."""
    refresh_subjects(session, as_of=AS_OF)
    for key in SUBJECT_LEXICON:
        subject = session.scalar(select(Subject).where(Subject.key == key))
        assert subject is not None
        assert subject.is_discovered is False


def test_first_seen_only_ever_moves_earlier(session: Session, corpus: None) -> None:
    """A subject's age is the lead time this product exists to measure.

    Letting a later run push first_seen_at forward would erase exactly that.
    """
    refresh_subjects(session, as_of=AS_OF)
    subject = session.scalar(select(Subject).where(Subject.key == "grid_storage"))
    assert subject is not None
    original = subject.first_seen_at

    refresh_subjects(session, as_of=AS_OF + timedelta(days=30))
    session.refresh(subject)
    assert subject.first_seen_at == original


def test_refreshing_twice_does_not_duplicate_a_subject(
    session: Session, corpus: None
) -> None:
    first = refresh_subjects(session, as_of=AS_OF)
    second = refresh_subjects(session, as_of=AS_OF)
    assert second.created == 0
    assert second.updated >= 1
    keys = [key for (key,) in session.execute(select(Subject.key)).all()]
    assert len(keys) == len(set(keys))
    assert first.created > 0


def test_a_company_name_never_becomes_a_subject(session: Session, corpus: None) -> None:
    """An issuer is an entity, resolved elsewhere. Modelling it twice lets one company's
    coverage read as a theme."""
    excluded = company_ngrams(session)
    assert "northbridge memory corp" in excluded
    assert "memory corp" in excluded, "fragments leak in as n-grams, so they must be covered"
    assert "northbridge" in excluded

    refresh_subjects(session, as_of=AS_OF)
    keys = {key for (key,) in session.execute(select(Subject.key)).all()}
    assert "northbridge" not in keys
    assert "northbridge_memory_corp" not in keys


def test_the_vocabulary_carries_discovered_and_declared_subjects(
    session: Session, corpus: None
) -> None:
    refresh_subjects(session, as_of=AS_OF)
    vocabulary = subject_vocabulary(session)

    assert vocabulary["grid_storage"] == ("grid storage",)
    # A declared subject keeps its curated vocabulary rather than being reduced to its key.
    assert set(vocabulary["memory"]) == set(SUBJECT_LEXICON["memory"])


def test_a_declared_topic_is_not_also_discovered_separately(
    session: Session, corpus: None
) -> None:
    """One topic, one subject. Otherwise its evidence splits across two keys and which one
    a sentence lands in comes down to a tie-break on key names."""
    when = AS_OF - timedelta(days=2)
    source = session.scalar(select(Source))
    assert source is not None
    for index in range(5, 10):
        session.add(
            SourceDocument(
                source_id=source.id,
                url=f"https://wire.test/ai{index}",
                title="Artificial intelligence spending",
                body_text=(
                    "Artificial intelligence deployments widened again. "
                    "Artificial intelligence budgets were raised."
                ),
                content_hash=f"{index:064d}",
                word_count=20,
                published_at=when,
                event_at=when,
                retrieved_at=when,
                ingested_at=when,
                data_mode=DataMode.LIVE,
                provider_key="feeds",
            )
        )
    session.flush()

    refresh_subjects(session, as_of=AS_OF)
    keys = {key for (key,) in session.execute(select(Subject.key)).all()}
    assert "ai_infrastructure" in keys
    assert "artificial_intelligence" not in keys


def test_a_subject_that_no_longer_qualifies_is_retracted(
    session: Session, corpus: None
) -> None:
    """Discovery output is derived data and must be withdrawable.

    Without this, a subject discovered once is a subject forever: a live run left website
    furniture — "cookie preference", "reprint permission advertising" — sitting in the table
    long after the filter that rejects them was in place, because refresh only ever added.
    """
    stale = Subject(
        key="cookie_preference",
        term="cookie preference",
        first_seen_at=AS_OF,
        last_seen_at=AS_OF,
        discovered_at=AS_OF,
        is_discovered=True,
        discovery_version="old",
        data_mode=DataMode.LIVE,
    )
    session.add(stale)
    session.flush()

    report = refresh_subjects(session, as_of=AS_OF)

    assert report.retracted >= 1
    assert session.scalar(select(Subject).where(Subject.key == "cookie_preference")) is None


def test_a_declared_subject_is_never_retracted(session: Session, corpus: None) -> None:
    """Lexicon subjects were not discovered, so discovery does not get to withdraw them."""
    refresh_subjects(session, as_of=AS_OF)
    keys = {key for (key,) in session.execute(select(Subject.key)).all()}
    assert set(SUBJECT_LEXICON) <= keys


def test_a_subject_still_referenced_by_evidence_survives(
    session: Session, corpus: None
) -> None:
    """Retracting a subject that evidence points at would strand those rows."""
    from marketradar.domain.models import EvidenceItem, SourceDocument

    document = session.scalars(select(SourceDocument)).first()
    assert document is not None
    session.add(
        EvidenceItem(
            document_id=document.id,
            claim="c",
            excerpt="e",
            excerpt_start=0,
            excerpt_end=1,
            confidence=0.8,
            direction=Direction.NEUTRAL,
            subject_key="niche_topic",
            extracted_by="test",
            extractor_version="test",
            rule_key="r#1",
            data_mode=DataMode.LIVE,
            event_at=AS_OF,
            published_at=AS_OF,
            retrieved_at=AS_OF,
        )
    )
    session.add(
        Subject(
            key="niche_topic",
            term="niche topic",
            first_seen_at=AS_OF,
            last_seen_at=AS_OF,
            discovered_at=AS_OF,
            is_discovered=True,
            discovery_version="old",
            data_mode=DataMode.LIVE,
        )
    )
    session.flush()

    refresh_subjects(session, as_of=AS_OF)
    assert session.scalar(select(Subject).where(Subject.key == "niche_topic")) is not None
