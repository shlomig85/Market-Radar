"""Persist value-chain edges read out of filings.

Turns :func:`marketradar.entities.relationships.extract_relationships` output into rows:
one ``EvidenceItem`` for the sentence that asserts the edge, and one ``EntityRelationship``
pointing at it. The ``evidence_id`` is not decoration — it is what lets a traversal answer
"why do you think this company supplies that one?" with a citation instead of an assertion.

Idempotent, like the rest of the pipeline: the evidence span is unique on
``(document, offset, rule, extractor version)`` and the edge is unique on its endpoints and
type, so re-running over the same corpus changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, Direction
from marketradar.domain.models import (
    Company,
    EntityRelationship,
    EvidenceItem,
    SourceDocument,
)
from marketradar.entities.relationships import (
    RELATIONSHIP_EXTRACTOR_NAME,
    RELATIONSHIP_EXTRACTOR_VERSION,
    extract_relationships,
)
from marketradar.ingestion.pipeline import build_resolver, filer_key_for
from marketradar.logging import get_logger

log = get_logger(__name__)


@dataclass
class RelationshipReport:
    """What relationship extraction actually did over a corpus."""

    documents_seen: int = 0
    documents_skipped: int = 0
    #: Documents whose filer could not be identified. These are not a failure — a news
    #: article has no filer — but first-person rules cannot run on them.
    documents_without_filer: int = 0
    evidence_created: int = 0
    edges_created: int = 0
    edges_reinforced: int = 0
    notes: list[str] = field(default_factory=list)


def extract_entity_relationships(session: Session) -> RelationshipReport:
    """Read relationships out of every stored document that does not yet have them."""
    report = RelationshipReport()
    resolver = build_resolver(session)
    known_companies = {key for (key,) in session.execute(select(Company.key)).all()}

    done = {
        document_id
        for (document_id,) in session.execute(
            select(EvidenceItem.document_id).where(
                EvidenceItem.extractor_version == RELATIONSHIP_EXTRACTOR_VERSION
            )
        ).all()
    }

    edges: dict[tuple[str, str, str, str, str], EntityRelationship] = {
        (
            edge.source_entity_type.value,
            edge.source_entity_key,
            edge.target_entity_type.value,
            edge.target_entity_key,
            edge.relationship_type.value,
        ): edge
        for edge in session.scalars(select(EntityRelationship)).all()
    }

    for document in session.scalars(select(SourceDocument)).all():
        report.documents_seen += 1
        if document.id in done:
            report.documents_skipped += 1
            continue

        filer_key = filer_key_for(document)
        if filer_key is None:
            report.documents_without_filer += 1
            continue
        if filer_key not in known_companies:
            # The filer is not in the company universe, so an edge would dangle. Sync the
            # company universe first; this is a data-ordering problem, not a parse failure.
            report.notes.append(f"Filer {filer_key} is not a known company; skipped")
            continue

        for found in extract_relationships(document.body_text, filer_key, resolver):
            # Company endpoints must exist; concept endpoints are graph nodes with no table.
            endpoints = [
                key
                for key, kind in (
                    (found.source_entity_key, found.source_entity_type),
                    (found.target_entity_key, found.target_entity_type),
                )
                if kind.value == "COMPANY"
            ]
            if any(key not in known_companies for key in endpoints):
                continue

            evidence = EvidenceItem(
                document_id=document.id,
                cluster_id=document.cluster_id,
                claim=found.claim,
                excerpt=found.excerpt,
                excerpt_start=found.excerpt_start,
                excerpt_end=found.excerpt_end,
                confidence=found.confidence,
                # A relationship disclosure is a standing fact, not an observed change, so
                # it carries no event type and can never reach the signal engine.
                event_type=None,
                direction=Direction.NEUTRAL,
                magnitude=None,
                entity_hint=found.party_surface,
                subject_key=None,
                rule_key=found.rule_key,
                extracted_by=RELATIONSHIP_EXTRACTOR_NAME,
                extractor_version=RELATIONSHIP_EXTRACTOR_VERSION,
                data_mode=document.data_mode,
                event_at=document.event_at,
                published_at=document.published_at,
                retrieved_at=document.retrieved_at,
            )
            session.add(evidence)
            session.flush()
            report.evidence_created += 1

            key = (
                found.source_entity_type.value,
                found.source_entity_key,
                found.target_entity_type.value,
                found.target_entity_key,
                found.relationship.value,
            )
            existing = edges.get(key)
            if existing is None:
                edge = EntityRelationship(
                    source_entity_type=found.source_entity_type,
                    source_entity_key=found.source_entity_key,
                    target_entity_type=found.target_entity_type,
                    target_entity_key=found.target_entity_key,
                    relationship_type=found.relationship,
                    weight=found.weight,
                    confidence=found.confidence,
                    evidence_id=evidence.id,
                    note=found.claim,
                    data_mode=document.data_mode,
                )
                session.add(edge)
                session.flush()
                edges[key] = edge
                report.edges_created += 1
            elif existing.evidence_id is None or found.confidence > existing.confidence:
                # Two cases converge here. A stronger disclosure of an already-evidenced
                # edge replaces the citation, so the edge points at the best evidence the
                # corpus holds for it. And an edge that had *no* evidence — a seeded one —
                # takes this citation whatever its confidence, then drops to that
                # citation's confidence: an edge is only as good as what supports it, and a
                # hand-entered prior of 0.95 backed by a sentence worth 0.78 is 0.78.
                existing.confidence = found.confidence
                existing.weight = max(existing.weight, found.weight)
                existing.evidence_id = evidence.id
                existing.note = found.claim
                existing.data_mode = DataMode.weakest(
                    [existing.data_mode, document.data_mode]
                )
                report.edges_reinforced += 1

    session.flush()
    log.info(
        "relationships.extracted",
        documents=report.documents_seen,
        edges_created=report.edges_created,
        edges_reinforced=report.edges_reinforced,
        evidence=report.evidence_created,
        without_filer=report.documents_without_filer,
    )
    return report


__all__ = ["RelationshipReport", "extract_entity_relationships"]
