"""Ingestion pipeline: provider documents -> documents -> clusters -> evidence -> events.

Every step is **idempotent**. Running the pipeline twice over the same corpus produces the
same database state, which is what makes it safe to re-run after a partial failure and what
a queue-backed worker will need in cycle 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.bus import DomainEvent, get_bus
from marketradar.config import Settings, get_settings
from marketradar.domain.enums import DataMode, Direction, EntityType
from marketradar.domain.models import (
    Company,
    Event,
    EventEvidence,
    EvidenceCluster,
    EvidenceItem,
    Source,
    SourceDocument,
)
from marketradar.errors import ValidationError
from marketradar.evidence.clustering import ClusterCandidate, assign_clusters
from marketradar.evidence.extractor import EXTRACTOR_NAME, EXTRACTOR_VERSION, extract
from marketradar.ingestion.hashing import content_hash
from marketradar.logging import get_logger
from marketradar.providers.base import ProviderResult, ProviderSource

log = get_logger(__name__)

PIPELINE_VERSION = "1.0.0"


@dataclass
class IngestionReport:
    """What a pipeline stage actually did. Returned, logged and asserted on in tests."""

    documents_seen: int = 0
    documents_created: int = 0
    documents_skipped: int = 0
    clusters_created: int = 0
    evidence_created: int = 0
    events_created: int = 0
    notes: list[str] = field(default_factory=list)

    def merge(self, other: IngestionReport) -> IngestionReport:
        return IngestionReport(
            documents_seen=self.documents_seen + other.documents_seen,
            documents_created=self.documents_created + other.documents_created,
            documents_skipped=self.documents_skipped + other.documents_skipped,
            clusters_created=self.clusters_created + other.clusters_created,
            evidence_created=self.evidence_created + other.evidence_created,
            events_created=self.events_created + other.events_created,
            notes=self.notes + other.notes,
        )


# ---------------------------------------------------------------- sources
def upsert_sources(session: Session, sources: tuple[ProviderSource, ...]) -> dict[str, Source]:
    """Insert or update source descriptors, returning them keyed by provider source key."""
    existing = {s.key: s for s in session.scalars(select(Source)).all()}
    result: dict[str, Source] = {}
    for descriptor in sources:
        row = existing.get(descriptor.key)
        if row is None:
            row = Source(
                key=descriptor.key,
                name=descriptor.name,
                publisher=descriptor.publisher,
                source_type=descriptor.source_type,
                source_class=descriptor.source_class,
                base_quality=descriptor.base_quality,
                homepage_url=descriptor.homepage_url,
                is_synthetic=descriptor.is_synthetic,
                data_mode=descriptor.data_mode,
                notes=descriptor.notes,
            )
            session.add(row)
        else:
            row.name = descriptor.name
            row.base_quality = descriptor.base_quality
            row.notes = descriptor.notes
        result[descriptor.key] = row
    session.flush()
    return result


# ------------------------------------------------------------- documents
def ingest_documents(
    session: Session,
    result: ProviderResult,
    sources: dict[str, Source],
    now: datetime | None = None,
) -> IngestionReport:
    """Persist provider documents with full provenance, skipping ones already stored."""
    report = IngestionReport(documents_seen=result.count)
    if result.mode == DataMode.UNAVAILABLE:
        report.notes.append(
            f"Provider {result.provider_key} is unavailable: {result.detail or 'no detail'}"
        )
        return report

    now = now or datetime.now(tz=UTC)
    for document in result.documents:
        source = sources.get(document.source_key)
        if source is None:
            raise ValidationError(
                "Provider emitted a document for an undeclared source",
                source_key=document.source_key,
                provider=result.provider_key,
            )
        if document.data_mode != source.data_mode:
            # A DEMO source may not emit LIVE documents, and vice versa. This is the code-side
            # counterpart of the database check constraints.
            raise ValidationError(
                "Document data mode does not match its source",
                document=document.external_id,
                document_mode=document.data_mode.value,
                source_mode=source.data_mode.value,
            )

        digest = content_hash(document.body_text)
        already = session.scalar(
            select(SourceDocument).where(
                SourceDocument.source_id == source.id,
                SourceDocument.content_hash == digest,
            )
        )
        if already is not None:
            report.documents_skipped += 1
            continue

        event_at = document.event_at or document.published_at
        row = SourceDocument(
            source_id=source.id,
            external_id=document.external_id,
            url=document.url,
            title=document.title,
            author=document.author,
            body_text=document.body_text,
            content_hash=digest,
            word_count=len(document.body_text.split()),
            language=document.language,
            published_at=document.published_at,
            event_at=event_at,
            # Recorded, never guessed silently: downstream recency uses event_at, and this
            # flag says whether that value was stated by the source or inferred by us.
            event_at_inferred=document.event_at is None,
            retrieved_at=now,
            ingested_at=now,
            data_mode=document.data_mode,
            provider_key=result.provider_key,
            provider_payload={
                **(document.payload or {}),
                "subject_hints": list(document.subject_hints),
            },
            origin_ref=document.origin_ref,
        )
        session.add(row)
        report.documents_created += 1

    session.flush()
    get_bus().publish(
        DomainEvent.SOURCE_INGESTED,
        {"provider": result.provider_key, "created": report.documents_created},
    )
    return report


# -------------------------------------------------------------- ancestry
def rebuild_clusters(session: Session, settings: Settings | None = None) -> IngestionReport:
    """Recompute information ancestry across every stored document.

    Recomputed wholesale rather than incrementally: clustering is a global property of the
    corpus, and at this scale correctness is worth far more than the saved milliseconds.
    """
    settings = settings or get_settings()
    report = IngestionReport()

    rows = session.scalars(select(SourceDocument)).all()
    if not rows:
        return report
    quality = {s.id: s.base_quality for s in session.scalars(select(Source)).all()}

    candidates = [
        ClusterCandidate(
            document_id=row.id,
            content_hash=row.content_hash,
            text=row.body_text,
            event_at=row.event_at,
            published_at=row.published_at,
            source_quality=quality.get(row.source_id, 50),
            origin_ref=row.origin_ref,
        )
        for row in rows
    ]
    assignments = assign_clusters(candidates, settings.duplicate_similarity_threshold)

    by_id = {row.id: row for row in rows}
    existing = {c.cluster_key: c for c in session.scalars(select(EvidenceCluster)).all()}

    for assignment in assignments:
        members = [by_id[doc_id] for doc_id in assignment.members]
        modes = [m.data_mode for m in members]
        cluster = existing.get(assignment.cluster_key)
        if cluster is None:
            cluster = EvidenceCluster(
                cluster_key=assignment.cluster_key, data_mode=DataMode.weakest(modes)
            )
            session.add(cluster)
            session.flush()
            report.clusters_created += 1
        cluster.label = assignment.label
        cluster.member_count = assignment.size
        cluster.first_seen_at = min(m.event_at for m in members)
        cluster.last_seen_at = max(m.event_at for m in members)
        cluster.data_mode = DataMode.weakest(modes)
        cluster.origin_document_id = assignment.origin_document_id

        for document_id, method in assignment.members.items():
            document = by_id[document_id]
            document.cluster_id = cluster.id
            document.cluster_method = method

    session.flush()
    # Evidence carries a denormalised cluster id for independence queries; keep it in step.
    for item in session.scalars(select(EvidenceItem)).all():
        item.cluster_id = by_id[item.document_id].cluster_id
    session.flush()
    return report


# -------------------------------------------------------------- evidence
def extract_evidence(session: Session) -> IngestionReport:
    """Extract evidence for documents that do not yet have it, at this extractor version."""
    report = IngestionReport()
    companies = session.scalars(select(Company)).all()
    lexicon: dict[str, str] = {}
    for company in companies:
        lexicon[company.name] = company.key
        if company.ticker:
            lexicon[company.ticker] = company.key

    done = {
        document_id
        for (document_id,) in session.execute(
            select(EvidenceItem.document_id).where(
                EvidenceItem.extractor_version == EXTRACTOR_VERSION
            )
        ).all()
    }

    for document in session.scalars(select(SourceDocument)).all():
        if document.id in done:
            continue
        hints = tuple((document.provider_payload or {}).get("subject_hints", ()))
        for item in extract(document.body_text, entity_lexicon=lexicon, subject_hints=hints):
            session.add(
                EvidenceItem(
                    document_id=document.id,
                    cluster_id=document.cluster_id,
                    claim=item.claim,
                    excerpt=item.excerpt,
                    excerpt_start=item.excerpt_start,
                    excerpt_end=item.excerpt_end,
                    confidence=item.confidence,
                    event_type=item.event_type,
                    direction=item.direction,
                    magnitude=item.magnitude,
                    entity_hint=item.entity_hint,
                    subject_key=item.subject_key,
                    rule_key=item.rule_key,
                    extracted_by=EXTRACTOR_NAME,
                    extractor_version=EXTRACTOR_VERSION,
                    data_mode=document.data_mode,
                    event_at=document.event_at,
                    published_at=document.published_at,
                    retrieved_at=document.retrieved_at,
                )
            )
            report.evidence_created += 1
    session.flush()
    get_bus().publish(DomainEvent.EVIDENCE_EXTRACTED, {"created": report.evidence_created})
    return report


# ---------------------------------------------------------------- events
def build_events(session: Session, as_of: datetime | None = None) -> IngestionReport:
    """Group evidence into typed events.

    The grouping key is ``(cluster, event_type, subject, event day)``. Because the cluster is
    part of the key, five rewrites of one announcement collapse into **one** event — which is
    the whole point of the ancestry model. Evidence with no event type (forward-looking or
    historical statements) never becomes an event.

    ``as_of`` is the instant the analysis is being performed at. It is recorded as
    ``detected_at`` so that a historical replay reports when the system *would have* detected
    the event, not when the replay happened to run — without which discovery lead time (the
    product's north-star metric) cannot be computed from a backtest.
    """
    as_of = as_of or datetime.now(tz=UTC)
    report = IngestionReport()
    existing = {
        key for (key,) in session.execute(select(Event.dedupe_key)).all()
    }

    grouped: dict[tuple[str, str, str, str], list[EvidenceItem]] = {}
    for item in session.scalars(select(EvidenceItem)).all():
        if item.event_type is None or item.cluster_id is None:
            continue
        day = item.event_at.date().isoformat()
        key = (item.cluster_id, item.event_type.value, item.subject_key or "unspecified", day)
        grouped.setdefault(key, []).append(item)

    for (cluster_id, event_type, subject, day), items in sorted(grouped.items()):
        dedupe_key = f"{cluster_id}:{event_type}:{subject}:{day}:{EXTRACTOR_VERSION}"
        if dedupe_key in existing:
            continue

        # The event inherits the strongest single piece of evidence supporting it, and a
        # confidence lifted slightly by corroboration *within* the cluster — capped, because
        # corroboration inside one cluster is not independent confirmation.
        best = max(items, key=lambda i: (i.confidence, i.magnitude or 0.0))
        corroboration = min(0.1, 0.02 * (len(items) - 1))
        entity_key = next((i.entity_hint for i in items if i.entity_hint), None)

        event = Event(
            event_type=best.event_type,
            direction=best.direction,
            magnitude=best.magnitude or 0.0,
            confidence=min(1.0, round(best.confidence + corroboration, 4)),
            entity_type=EntityType.COMPANY if entity_key else EntityType.INDUSTRY,
            entity_key=entity_key or subject,
            entity_label=entity_key or subject,
            subject_key=subject,
            occurred_at=min(i.event_at for i in items),
            # First publication among the supporting evidence: before this instant, nobody
            # could have known about this event.
            knowable_at=min(i.published_at for i in items),
            detected_at=as_of,
            cluster_id=cluster_id,
            data_mode=DataMode.weakest([i.data_mode for i in items]),
            dedupe_key=dedupe_key,
            extractor_version=EXTRACTOR_VERSION,
        )
        session.add(event)
        session.flush()
        for item in items:
            session.add(EventEvidence(event_id=event.id, evidence_id=item.id))
        report.events_created += 1

    session.flush()
    get_bus().publish(DomainEvent.EVENT_CREATED, {"created": report.events_created})
    return report


def net_direction(events: list[Event], sign_map: dict[str, int]) -> Direction:
    """Net direction of a set of events under a signal's sign mapping."""
    total = sum(sign_map.get(e.event_type.value, 0) * e.magnitude for e in events)
    if total > 0.05:
        return Direction.POSITIVE
    if total < -0.05:
        return Direction.NEGATIVE
    return Direction.NEUTRAL


__all__ = [
    "PIPELINE_VERSION",
    "IngestionReport",
    "build_events",
    "extract_evidence",
    "ingest_documents",
    "net_direction",
    "rebuild_clusters",
    "upsert_sources",
]
