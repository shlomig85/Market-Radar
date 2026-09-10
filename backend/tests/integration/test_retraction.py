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
    Subject,
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


def test_rebuild_withdraws_everything_derived_but_keeps_documents(
    session: Session, document: SourceDocument
) -> None:
    """The blunt instrument, for when version-gated retraction has not reached the data.

    Three runs in a row a correct fix failed to change stored output, because state
    accumulated in ways a version bump could not reach. `--rebuild` discards all derived
    data; documents are the expensive, rate-limited, non-derived part and are kept.
    """
    from marketradar.ingestion.retraction import rebuild_derived

    session.add(_evidence(document, EXTRACTOR_NAME, EXTRACTOR_VERSION))
    session.add(
        Subject(
            key="cookie_preference",
            term="cookie preference",
            first_seen_at=NOW,
            last_seen_at=NOW,
            discovered_at=NOW,
            is_discovered=True,
            discovery_version="v1",
            data_mode=DataMode.LIVE,
        )
    )
    session.flush()

    report = rebuild_derived(session)

    assert report.evidence_retracted >= 1
    assert session.scalar(select(func.count()).select_from(EvidenceItem)) == 0
    assert session.scalar(select(func.count()).select_from(Subject)) == 0
    # Documents survive: rebuilding must never mean re-fetching a rate-limited source.
    assert session.get(SourceDocument, document.id) is not None


def test_rebuild_keeps_hand_curated_edges(
    session: Session, document: SourceDocument
) -> None:
    """A human entered these. No extractor, and no rebuild, may withdraw them."""
    from marketradar.ingestion.retraction import rebuild_derived

    curated = _edge(None, None, "beta")
    derived = _edge(None, RELATIONSHIP_EXTRACTOR_VERSION, "gamma")
    session.add_all([curated, derived])
    session.flush()

    rebuild_derived(session)

    remaining = {
        edge.target_entity_key for edge in session.scalars(select(EntityRelationship)).all()
    }
    assert remaining == {"beta"}


def test_every_table_is_either_preserved_or_rebuilt() -> None:
    """No table may sit outside the rebuild's two lists without a decision being made.

    The hand-written model tuple this replaced went stale exactly this way: the research
    loop added `hypotheses` and `research_runs`, both pointing at `themes`, and neither was
    ever added to the delete list. A rebuild then died on `fk_hypotheses_theme_id_themes`.

    This test does not care which list a table lands in. It cares that somebody chose.
    """
    from marketradar.db.base import Base
    from marketradar.ingestion.retraction import (
        PRESERVED_TABLES,
        SELECTIVELY_CLEARED_TABLES,
    )

    known = PRESERVED_TABLES | SELECTIVELY_CLEARED_TABLES
    schema = {table.name for table in Base.metadata.sorted_tables}

    # A name in a list that no longer exists is also a defect: it silently preserves nothing.
    assert known - schema == set(), "these tables no longer exist in the schema"

    # Everything else is deleted by the rebuild, which is the safe default; this assertion
    # exists so the preserved list cannot quietly grow to cover the whole database.
    assert schema > PRESERVED_TABLES


def test_rebuild_survives_rows_that_reference_a_theme(
    session: Session, document: SourceDocument
) -> None:
    """The regression: a hypothesis outliving its theme blocked the whole rebuild.

    `hypotheses` and `research_runs` both carry a non-nullable FK to `themes`, so a theme
    cannot be deleted while either exists. Both are derived and must go.
    """
    from datetime import UTC, datetime

    from marketradar.domain.enums import DataMode, MarketAwareness, RunStatus, TrendMaturity
    from marketradar.domain.models import Hypothesis, ResearchRun, Theme
    from marketradar.ingestion.retraction import rebuild_derived

    now = datetime(2026, 9, 10, tzinfo=UTC)
    theme = Theme(
        slug="regression-theme",
        name="Regression Theme",
        maturity=TrendMaturity.ACCELERATING,
        market_awareness=MarketAwareness.DEVELOPING,
        market_awareness_mode=DataMode.DEMO,
        data_mode=DataMode.DEMO,
        first_detected_at=now,
        last_updated_at=now,
    )
    session.add(theme)
    session.flush()
    session.add(
        Hypothesis(
            theme_id=theme.id,
            statement="Something is happening.",
            status="OPEN",
            created_by="test",
            data_mode=DataMode.DEMO,
        )
    )
    session.add(
        ResearchRun(
            theme_id=theme.id,
            status=RunStatus.SUCCEEDED,
            started_at=now,
            data_mode=DataMode.DEMO,
            pipeline_version="test",
        )
    )
    session.flush()

    rebuild_derived(session)

    assert session.scalars(select(Theme)).all() == []
    assert session.scalars(select(Hypothesis)).all() == []
    assert session.scalars(select(ResearchRun)).all() == []
    # The documents the whole thing was derived from are untouched.
    assert session.get(SourceDocument, document.id) is not None
