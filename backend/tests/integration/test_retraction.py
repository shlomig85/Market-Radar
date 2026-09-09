"""Retracting the output of a superseded extractor.

This exists because of a specific, embarrassing failure: rules that produced a fabricated
graph edge were fixed, the fix was verified by tests, and the pipeline was re-run against
the database that held the bad edge — which reported ``created=0 skipped=72`` and left the
edge untouched. Extraction is idempotent per extractor version, so nothing re-ran.

The properties below are what make an extractor improvement actually reach stored data,
without a re-run being licence to destroy things that were not derived.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketradar.domain.enums import (
    ClaimType,
    DataMode,
    Direction,
    EntityType,
    EventType,
    FindingStance,
    MarketAwareness,
    RelationshipType,
    ResearchDimension,
    RunStatus,
    SourceClass,
    SourceType,
    TrendMaturity,
)
from marketradar.domain.models import (
    EntityRelationship,
    Event,
    EvidenceItem,
    FindingEvidence,
    ResearchFinding,
    ResearchRun,
    Source,
    SourceDocument,
    Theme,
)
from marketradar.entities.relationships import (
    RELATIONSHIP_EXTRACTOR_NAME,
    RELATIONSHIP_EXTRACTOR_VERSION,
)
from marketradar.evidence.extractor import EXTRACTOR_NAME, EXTRACTOR_VERSION
from marketradar.ingestion.retraction import retract_superseded

NOW = datetime(2026, 9, 9, tzinfo=UTC)
OLD_EVIDENCE_VERSION = "0.9.0"
OLD_RELATIONSHIP_VERSION = "rel-0.9.0"


@pytest.fixture
def document(session: Session) -> SourceDocument:
    source = Source(
        key="sec-edgar",
        name="SEC EDGAR",
        publisher="SEC",
        source_type=SourceType.SEC_FILING,
        source_class=SourceClass.REGULATORY,
        base_quality=95,
        is_synthetic=False,
        data_mode=DataMode.LIVE,
    )
    session.add(source)
    session.flush()
    row = SourceDocument(
        source_id=source.id,
        url="https://www.sec.gov/Archives/edgar/data/1/x.htm",
        title="A filing",
        body_text="Our principal suppliers include Alpha Corporation.",
        content_hash="d" * 64,
        word_count=7,
        published_at=NOW,
        event_at=NOW,
        retrieved_at=NOW,
        ingested_at=NOW,
        data_mode=DataMode.LIVE,
        provider_key="sec_edgar",
        provider_payload={"cik": "0000000001"},
    )
    session.add(row)
    session.flush()
    return row


def _evidence(
    document: SourceDocument, extracted_by: str, version: str, rule: str = "r#1"
) -> EvidenceItem:
    return EvidenceItem(
        document_id=document.id,
        claim="a claim",
        excerpt="an excerpt",
        excerpt_start=0,
        excerpt_end=10,
        confidence=0.8,
        event_type=None,
        direction=Direction.NEUTRAL,
        extracted_by=extracted_by,
        extractor_version=version,
        rule_key=rule,
        data_mode=DataMode.LIVE,
        event_at=NOW,
        published_at=NOW,
        retrieved_at=NOW,
    )


def _edge(
    evidence_id: str | None, extractor_version: str | None, target: str
) -> EntityRelationship:
    return EntityRelationship(
        source_entity_type=EntityType.COMPANY,
        source_entity_key="alpha",
        target_entity_type=EntityType.COMPANY,
        target_entity_key=target,
        relationship_type=RelationshipType.SUPPLIES,
        weight=0.6,
        confidence=0.7,
        evidence_id=evidence_id,
        extractor_version=extractor_version,
        data_mode=DataMode.LIVE,
    )


# ------------------------------------------------------------- no-op case
def test_nothing_is_retracted_when_versions_match(
    session: Session, document: SourceDocument
) -> None:
    """A routine re-run must not churn the database."""
    session.add(_evidence(document, EXTRACTOR_NAME, EXTRACTOR_VERSION))
    session.add(
        _evidence(document, RELATIONSHIP_EXTRACTOR_NAME, RELATIONSHIP_EXTRACTOR_VERSION, "r#2")
    )
    session.flush()

    report = retract_superseded(session)

    assert not report.anything_retracted
    assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 2


# ---------------------------------------------------------- the core case
def test_superseded_evidence_is_withdrawn(
    session: Session, document: SourceDocument
) -> None:
    session.add(_evidence(document, EXTRACTOR_NAME, OLD_EVIDENCE_VERSION))
    session.add(_evidence(document, EXTRACTOR_NAME, EXTRACTOR_VERSION, "r#2"))
    session.flush()

    report = retract_superseded(session)

    assert report.evidence_retracted == 1
    remaining = session.scalars(select(EvidenceItem)).all()
    assert [item.extractor_version for item in remaining] == [EXTRACTOR_VERSION]


def test_an_edge_produced_by_a_superseded_extractor_is_deleted(
    session: Session, document: SourceDocument
) -> None:
    """The fabricated-edge case. It was derived, it was wrong, it goes."""
    stale = _evidence(document, RELATIONSHIP_EXTRACTOR_NAME, OLD_RELATIONSHIP_VERSION)
    session.add(stale)
    session.flush()
    session.add(_edge(stale.id, OLD_RELATIONSHIP_VERSION, "beta"))
    session.flush()

    report = retract_superseded(session)

    assert report.edges_retracted == 1
    assert session.scalars(select(EntityRelationship)).all() == []


def test_a_curated_edge_survives_but_loses_its_citation(
    session: Session, document: SourceDocument
) -> None:
    """A human-entered edge is not derived data and must never be deleted by a re-run.

    It may, however, have been cited by evidence that is now withdrawn — so it keeps its
    place in the graph and loses the citation, rather than keeping a dangling one.
    """
    stale = _evidence(document, RELATIONSHIP_EXTRACTOR_NAME, OLD_RELATIONSHIP_VERSION)
    session.add(stale)
    session.flush()
    curated = _edge(stale.id, None, "gamma")
    session.add(curated)
    session.flush()

    report = retract_superseded(session)

    assert report.edges_retracted == 0
    assert report.edges_uncited == 1
    session.refresh(curated)
    assert curated.evidence_id is None
    assert curated.target_entity_key == "gamma"


def test_events_from_a_superseded_extractor_are_withdrawn(
    session: Session, document: SourceDocument
) -> None:
    for version in (OLD_EVIDENCE_VERSION, EXTRACTOR_VERSION):
        session.add(
            Event(
                event_type=EventType.DEMAND_ACCELERATION,
                direction=Direction.POSITIVE,
                magnitude=0.5,
                confidence=0.8,
                entity_type=EntityType.INDUSTRY,
                entity_key="memory",
                entity_label="memory",
                subject_key="memory",
                occurred_at=NOW,
                knowable_at=NOW,
                detected_at=NOW,
                data_mode=DataMode.LIVE,
                dedupe_key=f"k:{version}",
                extractor_version=version,
            )
        )
    session.flush()

    report = retract_superseded(session)

    assert report.events_retracted == 1
    assert [e.extractor_version for e in session.scalars(select(Event)).all()] == [
        EXTRACTOR_VERSION
    ]


def test_source_documents_are_never_retracted(
    session: Session, document: SourceDocument
) -> None:
    """Documents are the raw material. Everything else is disposable; they are not."""
    session.add(_evidence(document, EXTRACTOR_NAME, OLD_EVIDENCE_VERSION))
    session.flush()

    retract_superseded(session)

    assert session.get(SourceDocument, document.id) is not None
    assert session.scalar(select(func.count()).select_from(SourceDocument)) == 1


# ------------------------------------------------------------- downstream
@pytest.fixture
def research_run(session: Session) -> ResearchRun:
    theme = Theme(
        slug="t",
        name="T",
        maturity=TrendMaturity.INVISIBLE,
        market_awareness=MarketAwareness.UNKNOWN,
        first_detected_at=NOW,
        last_updated_at=NOW,
        data_mode=DataMode.LIVE,
    )
    session.add(theme)
    session.flush()
    run = ResearchRun(
        theme_id=theme.id,
        status=RunStatus.SUCCEEDED,
        started_at=NOW,
        data_mode=DataMode.LIVE,
        pipeline_version="test",
    )
    session.add(run)
    session.flush()
    return run


def _finding(session: Session, run: ResearchRun, key: str) -> ResearchFinding:
    finding = ResearchFinding(
        research_run_id=run.id,
        dimension=ResearchDimension.DEMAND,
        claim=f"finding {key}",
        claim_type=ClaimType.FACT,
        stance=FindingStance.SUPPORTING,
        confidence=0.7,
        generated_by="test",
        data_mode=DataMode.LIVE,
    )
    session.add(finding)
    session.flush()
    return finding


def test_a_finding_left_with_no_evidence_is_withdrawn_with_it(
    session: Session, document: SourceDocument, research_run: ResearchRun
) -> None:
    """A claim whose entire support was withdrawn is not a claim any more."""
    stale = _evidence(document, EXTRACTOR_NAME, OLD_EVIDENCE_VERSION)
    session.add(stale)
    session.flush()
    doomed = _finding(session, research_run, "doomed")
    session.add(
        FindingEvidence(
            finding_id=doomed.id, evidence_id=stale.id, role=FindingStance.SUPPORTING
        )
    )
    session.flush()

    report = retract_superseded(session)

    assert report.findings_retracted == 1
    assert session.scalars(select(ResearchFinding)).all() == []


def test_a_finding_that_keeps_support_survives(
    session: Session, document: SourceDocument, research_run: ResearchRun
) -> None:
    stale = _evidence(document, EXTRACTOR_NAME, OLD_EVIDENCE_VERSION)
    current = _evidence(document, EXTRACTOR_NAME, EXTRACTOR_VERSION, "r#2")
    session.add_all([stale, current])
    session.flush()
    survivor = _finding(session, research_run, "survivor")
    session.add_all(
        [
            FindingEvidence(
                finding_id=survivor.id, evidence_id=stale.id, role=FindingStance.SUPPORTING
            ),
            FindingEvidence(
                finding_id=survivor.id,
                evidence_id=current.id,
                role=FindingStance.SUPPORTING,
            ),
        ]
    )
    session.flush()

    report = retract_superseded(session)

    assert report.findings_retracted == 0
    assert session.get(ResearchFinding, survivor.id) is not None
