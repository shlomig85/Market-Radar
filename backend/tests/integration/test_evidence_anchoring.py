"""Anchoring a theme on the companies its own evidence names.

The failure this pins was found on a live SEC run, not in a test: 72 real filings produced
85 events, a genuine theme — and **zero** company exposures. Theme anchors were concept
nodes only ("memory requirement", "HBM"), and a knowledge graph read out of real filings
contains company-to-company edges and almost no concept nodes, so the traversal started at
entities the graph did not contain and reached nothing.

These tests assert the property that failure violated: a theme built from evidence about
real companies must reach those companies, whether or not a concept graph exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import (
    DataMode,
    Direction,
    EntityType,
    EventType,
    MarketAwareness,
    RelationshipType,
    TrendMaturity,
)
from marketradar.domain.models import (
    Company,
    EntityRelationship,
    Event,
    EvidenceCluster,
    Theme,
)
from marketradar.mapping.exposure import compute_exposures, evidence_anchors
from marketradar.mapping.value_chain import Anchor

AS_OF = datetime(2026, 9, 9, tzinfo=UTC)
WINDOW_START = AS_OF - timedelta(days=90)


def _cluster(session: Session, key: str) -> EvidenceCluster:
    """Ancestry clusters are what independence is counted over, so tests must create them."""
    return EvidenceCluster(id=key, cluster_key=key, member_count=1, data_mode=DataMode.LIVE)


def _event(
    entity_key: str,
    cluster: str,
    day: int,
    subject: str = "memory",
    cluster_id: str | None = None,
) -> Event:
    when = WINDOW_START + timedelta(days=day)
    return Event(
        event_type=EventType.DEMAND_ACCELERATION,
        direction=Direction.POSITIVE,
        magnitude=0.8,
        confidence=0.85,
        entity_type=EntityType.COMPANY,
        entity_key=entity_key,
        entity_label=entity_key,
        subject_key=subject,
        occurred_at=when,
        knowable_at=when,
        detected_at=when,
        cluster_id=cluster_id,
        data_mode=DataMode.LIVE,
        dedupe_key=f"{entity_key}:{cluster}:{day}",
        extractor_version="test",
    )


@pytest.fixture
def universe(session: Session) -> Theme:
    """Two real issuers, a supply edge between them, and NO concept nodes at all."""
    for key, name in (
        ("sec-0001045810", "NVIDIA CORP"),
        ("sec-0000723125", "MICRON TECHNOLOGY INC"),
    ):
        session.add(
            Company(key=key, name=name, is_fictional=False, data_mode=DataMode.LIVE)
        )
    session.add(
        EntityRelationship(
            source_entity_type=EntityType.COMPANY,
            source_entity_key="sec-0000723125",
            target_entity_type=EntityType.COMPANY,
            target_entity_key="sec-0001045810",
            relationship_type=RelationshipType.SUPPLIES,
            weight=0.6,
            confidence=0.69,
            data_mode=DataMode.LIVE,
        )
    )
    theme = Theme(
        slug="memory-change",
        name="Memory Change",
        anchor_entities={"subjects": ["memory"]},
        maturity=TrendMaturity.INVISIBLE,
        market_awareness=MarketAwareness.UNKNOWN,
        first_detected_at=AS_OF,
        last_updated_at=AS_OF,
        data_mode=DataMode.LIVE,
    )
    session.add(theme)
    # Two independent clusters name NVIDIA; nothing names Micron directly.
    for index, cluster in enumerate(("c1", "c2")):
        session.add(_cluster(session, cluster))
        session.flush()  # the event's FK needs the cluster to exist first
        session.add(_event("sec-0001045810", cluster, index * 5, cluster_id=cluster))
    session.flush()
    return theme


def _anchors(session: Session) -> list[Anchor]:
    return evidence_anchors(
        session,
        subjects={"memory"},
        window_start=WINDOW_START,
        as_of=AS_OF,
        subject_weights={"memory": 1.0},
    )


def test_a_company_named_by_evidence_becomes_an_anchor(
    session: Session, universe: Theme
) -> None:
    anchors = _anchors(session)
    assert [a.entity_key for a in anchors] == ["sec-0001045810"]
    assert anchors[0].entity_type is EntityType.COMPANY
    assert anchors[0].data_mode is DataMode.LIVE
    assert anchors[0].reason and "evidence cluster" in anchors[0].reason


def test_a_theme_with_no_concept_graph_still_reaches_companies(
    session: Session, universe: Theme
) -> None:
    """The live failure, inverted into an assertion."""
    exposures = compute_exposures(session, universe, _anchors(session), AS_OF)
    assert exposures, "a theme whose evidence names real companies must reach them"

    companies = {c.id: c.key for c in session.scalars(select(Company)).all()}
    by_key = {companies[e.company_id]: e for e in exposures}
    assert "sec-0001045810" in by_key


def test_the_supplier_is_reached_through_the_extracted_edge(
    session: Session, universe: Theme
) -> None:
    """The anchor is the entry point; the extracted edges do the rest of the work."""
    exposures = compute_exposures(session, universe, _anchors(session), AS_OF)
    companies = {c.id: c.key for c in session.scalars(select(Company)).all()}
    by_key = {companies[e.company_id]: e for e in exposures}

    assert "sec-0000723125" in by_key, "Micron supplies NVIDIA and must be second-order"
    supplier = by_key["sec-0000723125"]
    assert supplier.order_of_effect == 2
    assert supplier.path["hops"], "a traversed exposure keeps its hops"
    assert supplier.path["anchored_by"] == "traversal"


def test_an_evidence_anchored_exposure_is_never_unexplained(
    session: Session, universe: Theme
) -> None:
    """It carries no hops, so it must carry a reason instead."""
    exposures = compute_exposures(session, universe, _anchors(session), AS_OF)
    companies = {c.id: c.key for c in session.scalars(select(Company)).all()}
    anchored = next(
        e for e in exposures if companies[e.company_id] == "sec-0001045810"
    )
    assert anchored.path["anchored_by"] == "evidence"
    assert anchored.path["hops"] == []
    assert "evidence cluster" in anchored.rationale
    assert anchored.data_mode is DataMode.LIVE


def test_anchor_weight_saturates_in_independent_clusters(
    session: Session, universe: Theme
) -> None:
    """One report must not anchor a theme as hard as five."""
    one = _anchors(session)[0].weight
    for index in range(3):
        key = f"extra{index}"
        session.add(_cluster(session, key))
        session.flush()
        session.add(_event("sec-0001045810", key, 20 + index, cluster_id=key))
    session.flush()
    many = _anchors(session)[0].weight
    assert many > one
    assert many < 1.0, "weight saturates rather than reaching certainty"


def test_future_evidence_cannot_anchor_a_historical_theme(
    session: Session, universe: Theme
) -> None:
    """Temporal integrity applies to anchoring exactly as it does to signals."""
    future = _event("sec-0000723125", "future", 0)
    future.knowable_at = AS_OF + timedelta(days=30)
    future.occurred_at = AS_OF + timedelta(days=30)
    session.add(future)
    session.flush()
    assert [a.entity_key for a in _anchors(session)] == ["sec-0001045810"]


def test_an_unknown_company_is_never_anchored(session: Session, universe: Theme) -> None:
    """Evidence naming an issuer outside the universe must not create a dangling anchor."""
    session.add(_event("sec-9999999999", "ghost", 3))
    session.flush()
    assert all(a.entity_key != "sec-9999999999" for a in _anchors(session))
