"""Temporal integrity: information published after T must never enter analysis as of T.

This is the defect the Cycle-1 audit found and it is the most damaging one possible in a
system whose entire value proposition is *earliness*. With lookahead contamination,
backtests are meaningless, discovery lead time is unmeasurable, and every "we would have
caught this early" claim is fabricated by construction.

Measured before the fix, running the pipeline as of 2026-08-10 over the development corpus:

    15 of 34 documents ingested had been published AFTER that date
    29 of 42 events were sourced from documents that did not yet exist

These tests exist so that can never silently return.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from marketradar.demo.loader import seed_demo_universe
from marketradar.domain.models import Event, EventEvidence, EvidenceItem, SourceDocument
from marketradar.orchestration import run_pipeline
from marketradar.providers import build_default_registry

pytestmark = [pytest.mark.integration, pytest.mark.e2e]

#: Deliberately mid-corpus: documents exist both before and after it, and the corpus
#: contains documents whose event time precedes this instant while their publication time
#: follows it — the exact shape that defeats an occurred_at-only filter.
AS_OF = datetime(2026, 8, 10, tzinfo=UTC)
LATER = datetime(2026, 9, 8, tzinfo=UTC)


@pytest.fixture
def run_as_of(session, settings):
    def _run(moment: datetime):
        registry = build_default_registry(settings)
        return run_pipeline(session, as_of=moment, settings=settings, registry=registry)

    seed_demo_universe(session)
    return _run


def test_no_document_published_after_the_as_of_instant_is_ingested(session, run_as_of):
    run_as_of(AS_OF)
    leaked = session.scalars(
        select(SourceDocument).where(SourceDocument.published_at > AS_OF)
    ).all()
    assert leaked == [], (
        f"{len(leaked)} document(s) published after the as-of instant were ingested: "
        f"{[d.external_id for d in leaked[:5]]}"
    )


def test_no_event_is_knowable_after_the_as_of_instant(session, run_as_of):
    run_as_of(AS_OF)
    events = session.scalars(select(Event)).all()
    assert events, "the run must produce events, or this test proves nothing"
    for event in events:
        assert event.knowable_at <= AS_OF, (
            f"event {event.event_type} became knowable at {event.knowable_at}, "
            f"after the as-of instant {AS_OF}"
        )


def test_no_event_is_supported_by_a_not_yet_published_document(session, run_as_of):
    """The audit's original query, as a permanent assertion."""
    run_as_of(AS_OF)
    contaminated = session.execute(
        select(func.count(func.distinct(Event.id)))
        .select_from(Event)
        .join(EventEvidence, EventEvidence.event_id == Event.id)
        .join(EvidenceItem, EvidenceItem.id == EventEvidence.evidence_id)
        .join(SourceDocument, SourceDocument.id == EvidenceItem.document_id)
        .where(SourceDocument.published_at > AS_OF)
    ).scalar_one()
    assert contaminated == 0


def test_knowable_at_is_the_first_publication_not_the_event_date(session, run_as_of):
    """An event is knowable when first *reported*, which can be well after it occurred."""
    run_as_of(LATER)
    rows = session.execute(
        select(Event.id, Event.occurred_at, Event.knowable_at, func.min(EvidenceItem.published_at))
        .join(EventEvidence, EventEvidence.event_id == Event.id)
        .join(EvidenceItem, EvidenceItem.id == EventEvidence.evidence_id)
        .group_by(Event.id, Event.occurred_at, Event.knowable_at)
    ).all()
    assert rows
    for _event_id, occurred_at, knowable_at, first_published in rows:
        assert knowable_at == first_published
        # Reporting cannot precede occurrence in this corpus, and must never be assumed to.
        assert knowable_at >= occurred_at - timedelta(days=1)


def test_detected_at_records_the_as_of_instant_not_wall_clock(session, run_as_of):
    """Without this, discovery lead time cannot be computed from a historical replay."""
    run_as_of(AS_OF)
    events = session.scalars(select(Event)).all()
    assert events
    assert all(e.detected_at == AS_OF for e in events), (
        "detected_at must be the as-of instant; using wall-clock time makes a backtest "
        "report the replay date as the discovery date"
    )


def test_an_earlier_as_of_sees_strictly_less(session, run_as_of):
    """The system must know less at an earlier point in time. Anything else is lookahead."""
    early = run_as_of(AS_OF)
    early_docs = session.scalar(select(func.count()).select_from(SourceDocument))
    early_events = session.scalar(select(func.count()).select_from(Event))

    run_as_of(LATER)
    late_docs = session.scalar(select(func.count()).select_from(SourceDocument))
    late_events = session.scalar(select(func.count()).select_from(Event))

    assert early.ingestion.documents_created > 0
    assert early_docs < late_docs, "a later as-of must see strictly more documents"
    assert early_events < late_events, "a later as-of must see strictly more events"
