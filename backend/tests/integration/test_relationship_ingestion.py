"""Persisting extracted value-chain edges (audit C5).

The audit counted zero edges carrying evidence. These tests assert the property that
finding was really about: an edge must be reachable back to the sentence that asserts it,
and re-running must not change the graph.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import (
    DataMode,
    EntityType,
    RelationshipType,
    SourceClass,
    SourceType,
)
from marketradar.domain.models import (
    Company,
    EntityRelationship,
    EvidenceItem,
    Source,
    SourceDocument,
)
from marketradar.entities.relationships import RELATIONSHIP_EXTRACTOR_VERSION
from marketradar.ingestion.relationships import (
    extract_entity_relationships,
    filer_key_for,
)

FILER_CIK = "0000320193"
FILER_KEY = f"sec-{FILER_CIK}"

FILING_BODY = """
Item 1. Business.

We design and manufacture high-bandwidth memory and DRAM modules for data centre
customers.

Our principal suppliers include Samsung Electronics and Taiwan Semiconductor
Manufacturing Company. We purchase substantially all of our leading-edge wafers from
Taiwan Semiconductor Manufacturing Company.

NVIDIA Corporation accounted for approximately 18% of our total net revenue in fiscal
2025. The Company competes primarily with Microsoft Corporation in cloud services.

We no longer purchase display panels from Samsung Electronics for our retail products.
"""


@pytest.fixture
def corpus(session: Session) -> SourceDocument:
    """One SEC-shaped filing, with the companies it names already in the universe."""
    for key, name, ticker in (
        (FILER_KEY, "Apple Inc.", "AAPL"),
        ("sec-0000789019", "Microsoft Corporation", "MSFT"),
        ("samsung", "Samsung Electronics", None),
        ("tsmc", "Taiwan Semiconductor Manufacturing Company", "TSM"),
        ("nvidia", "NVIDIA Corporation", "NVDA"),
    ):
        session.add(
            Company(
                key=key,
                name=name,
                ticker=ticker,
                is_fictional=False,
                data_mode=DataMode.LIVE,
            )
        )

    source = Source(
        key="sec-edgar",
        name="SEC EDGAR",
        publisher="U.S. Securities and Exchange Commission",
        source_type=SourceType.SEC_FILING,
        source_class=SourceClass.REGULATORY,
        base_quality=95,
        is_synthetic=False,
        data_mode=DataMode.LIVE,
    )
    session.add(source)
    session.flush()

    filed = datetime(2025, 11, 3, tzinfo=UTC)
    document = SourceDocument(
        source_id=source.id,
        external_id=f"sec:{FILER_CIK}:0000320193-25-000001",
        url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000001/aapl-10k.htm",
        title="Apple Inc. — 10-K",
        body_text=FILING_BODY,
        content_hash="a" * 64,
        word_count=len(FILING_BODY.split()),
        published_at=filed,
        event_at=filed,
        retrieved_at=filed,
        ingested_at=filed,
        data_mode=DataMode.LIVE,
        provider_key="sec_edgar",
        provider_payload={"form": "10-K", "cik": FILER_CIK},
    )
    session.add(document)
    session.flush()
    return document


# ------------------------------------------------------------ filer keys
def test_filer_key_is_derived_from_the_cik(corpus: SourceDocument) -> None:
    """The filing's CIK and the company provider's key must agree, or edges dangle."""
    assert filer_key_for(corpus) == FILER_KEY


def test_a_document_with_no_filer_has_no_key(session: Session) -> None:
    document = SourceDocument(
        source_id="x", url="u", title="t", body_text="b", content_hash="c",
        published_at=datetime.now(tz=UTC), event_at=datetime.now(tz=UTC),
        retrieved_at=datetime.now(tz=UTC), ingested_at=datetime.now(tz=UTC),
        data_mode=DataMode.LIVE, provider_key="news", provider_payload={"outlet": "x"},
    )
    assert filer_key_for(document) is None


# --------------------------------------------------------------- edges
def test_edges_are_created_from_the_filing(session: Session, corpus: SourceDocument) -> None:
    report = extract_entity_relationships(session)
    assert report.edges_created > 0

    edges = {
        (e.source_entity_key, e.relationship_type, e.target_entity_key)
        for e in session.scalars(select(EntityRelationship)).all()
    }
    assert ("samsung", RelationshipType.SUPPLIES, FILER_KEY) in edges
    assert ("tsmc", RelationshipType.SUPPLIES, FILER_KEY) in edges
    assert ("nvidia", RelationshipType.BUYS_FROM, FILER_KEY) in edges
    assert (FILER_KEY, RelationshipType.COMPETES_WITH, "sec-0000789019") in edges
    assert (FILER_KEY, RelationshipType.PRODUCES, "hbm") in edges


def test_every_extracted_edge_carries_resolvable_evidence(
    session: Session, corpus: SourceDocument
) -> None:
    """The whole point of C5: no edge without a citation."""
    extract_entity_relationships(session)

    edges = session.scalars(select(EntityRelationship)).all()
    assert edges
    for edge in edges:
        assert edge.evidence_id is not None, f"{edge.source_entity_key} -> {edge.target_entity_key}"
        evidence = session.get(EvidenceItem, edge.evidence_id)
        assert evidence is not None
        # The excerpt must be text that is actually in the document, at the offsets stored.
        assert evidence.excerpt in corpus.body_text
        assert (
            corpus.body_text[evidence.excerpt_start : evidence.excerpt_end].strip()
            == evidence.excerpt
        )


def test_relationship_evidence_can_never_become_a_signal(
    session: Session, corpus: SourceDocument
) -> None:
    """A standing relationship is not an observed change; it must not reach the engine."""
    extract_entity_relationships(session)
    items = session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.extractor_version == RELATIONSHIP_EXTRACTOR_VERSION
        )
    ).all()
    assert items
    assert all(item.event_type is None for item in items)


def test_edges_inherit_the_documents_data_mode(
    session: Session, corpus: SourceDocument
) -> None:
    """A LIVE filing must not produce edges that look synthetic, or the reverse."""
    extract_entity_relationships(session)
    modes = {e.data_mode for e in session.scalars(select(EntityRelationship)).all()}
    assert modes == {DataMode.LIVE}


def test_negated_disclosures_do_not_reach_the_graph(
    session: Session, corpus: SourceDocument
) -> None:
    """'We no longer purchase display panels from Samsung' is in the corpus above."""
    extract_entity_relationships(session)
    notes = [
        e.note or ""
        for e in session.scalars(select(EntityRelationship)).all()
        if e.source_entity_key == "samsung"
    ]
    assert notes  # Samsung IS a supplier, on the strength of a different sentence
    assert not any("display" in note for note in notes)


def test_extraction_is_idempotent(session: Session, corpus: SourceDocument) -> None:
    first = extract_entity_relationships(session)
    edges_after_first = len(session.scalars(select(EntityRelationship)).all())
    evidence_after_first = len(session.scalars(select(EvidenceItem)).all())

    second = extract_entity_relationships(session)

    assert second.documents_skipped == 1
    assert second.edges_created == 0
    assert second.evidence_created == 0
    assert len(session.scalars(select(EntityRelationship)).all()) == edges_after_first
    assert len(session.scalars(select(EvidenceItem)).all()) == evidence_after_first
    assert first.edges_created == edges_after_first


def test_an_unknown_filer_is_reported_not_silently_dropped(
    session: Session, corpus: SourceDocument
) -> None:
    """A filing from an issuer outside the company universe would produce dangling edges."""
    session.execute(select(Company))
    filer = session.scalar(select(Company).where(Company.key == FILER_KEY))
    assert filer is not None
    session.delete(filer)
    session.flush()

    report = extract_entity_relationships(session)
    assert report.edges_created == 0
    assert any(FILER_KEY in note for note in report.notes)


def test_counterparties_outside_the_universe_do_not_create_dangling_edges(
    session: Session, corpus: SourceDocument
) -> None:
    unknown = session.scalar(select(Company).where(Company.key == "tsmc"))
    assert unknown is not None
    session.delete(unknown)
    session.flush()

    extract_entity_relationships(session)
    keys = {
        key
        for edge in session.scalars(select(EntityRelationship)).all()
        for key, kind in (
            (edge.source_entity_key, edge.source_entity_type),
            (edge.target_entity_key, edge.target_entity_type),
        )
        if kind is EntityType.COMPANY
    }
    known = {key for (key,) in session.execute(select(Company.key)).all()}
    assert keys <= known


def test_a_hand_entered_edge_takes_the_citation_and_its_confidence(
    session: Session, corpus: SourceDocument
) -> None:
    """A seeded edge with a confident-looking prior and no evidence is the C5 failure mode.

    Once a filing actually asserts the edge, the edge must point at that sentence *and*
    fall back to what the sentence supports. Keeping the hand-entered 0.95 while citing a
    0.78 sentence would present a guess as a finding.
    """
    seeded = EntityRelationship(
        source_entity_type=EntityType.COMPANY,
        source_entity_key="samsung",
        target_entity_type=EntityType.COMPANY,
        target_entity_key=FILER_KEY,
        relationship_type=RelationshipType.SUPPLIES,
        weight=0.9,
        confidence=0.95,
        evidence_id=None,
        note="Hand-entered during bootstrap.",
        data_mode=DataMode.LIVE,
    )
    session.add(seeded)
    session.flush()

    report = extract_entity_relationships(session)
    assert report.edges_reinforced >= 1

    session.refresh(seeded)
    assert seeded.evidence_id is not None
    assert seeded.confidence < 0.95
    evidence = session.get(EvidenceItem, seeded.evidence_id)
    assert evidence is not None
    assert "Samsung" in evidence.excerpt
