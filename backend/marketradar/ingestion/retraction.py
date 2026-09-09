"""Retracting the output of a superseded extractor.

Extraction is idempotent on ``(document, span, rule, extractor version)``: re-running over
an unchanged corpus changes nothing. That is correct — and it silently becomes *wrong* the
moment an extractor's behaviour changes without its version changing, because every
document then looks "already done" and the improvement never reaches the stored data.

That is not hypothetical. The first real-filing run stored a fabricated customer edge; the
rules were fixed, the fix was verified by tests, the pipeline was re-run against the same
database — and it reported ``created=0 skipped=72`` and left the fabricated edge exactly
where it was. A version bump alone does not fix it either: the superseded rows do not
delete themselves, so the corpus ends up carrying two generations of evidence at once and
double-counting them into events.

So an extractor version bump means: **retract everything the previous version produced,
then extract again.** Derived data is disposable; the documents it was derived from are not.

What is never retracted here: source documents, clusters, companies, and any edge with no
``extractor_version`` — that is, one a human curated rather than one this system inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from marketradar.domain.models import (
    EntityRelationship,
    Event,
    EvidenceItem,
    FindingEvidence,
    ResearchFinding,
    ResearchReport,
    Score,
    Signal,
    SignalObservation,
    Subject,
    Theme,
    ThemeCompanyExposure,
    ThemeSignal,
    Trend,
)
from marketradar.entities.relationships import (
    RELATIONSHIP_EXTRACTOR_NAME,
    RELATIONSHIP_EXTRACTOR_VERSION,
)
from marketradar.evidence.extractor import EXTRACTOR_NAME, EXTRACTOR_VERSION
from marketradar.logging import get_logger

log = get_logger(__name__)


@dataclass
class RetractionReport:
    """What was withdrawn, so a re-run is never a silent rewrite of history."""

    events_retracted: int = 0
    evidence_retracted: int = 0
    edges_retracted: int = 0
    edges_uncited: int = 0
    findings_retracted: int = 0
    #: Reports assembled before this retraction may cite evidence that no longer exists.
    #: Counted and surfaced rather than deleted: a report is a published artefact, and
    #: quietly destroying one is worse than telling its reader it is stale.
    stale_reports: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def anything_retracted(self) -> bool:
        return bool(
            self.events_retracted
            or self.evidence_retracted
            or self.edges_retracted
            or self.edges_uncited
        )


def retract_superseded(session: Session) -> RetractionReport:
    """Withdraw every artefact produced by an extractor version we no longer run."""
    report = RetractionReport()

    # --- 1. edges produced by a superseded relationship extractor -------------
    stale_edges = session.scalars(
        select(EntityRelationship).where(
            EntityRelationship.extractor_version.is_not(None),
            EntityRelationship.extractor_version != RELATIONSHIP_EXTRACTOR_VERSION,
        )
    ).all()
    for edge in stale_edges:
        session.delete(edge)
    report.edges_retracted = len(stale_edges)
    session.flush()

    # --- 2. evidence produced by a superseded extractor ----------------------
    superseded_evidence = session.scalars(
        select(EvidenceItem.id).where(
            (
                (EvidenceItem.extracted_by == EXTRACTOR_NAME)
                & (EvidenceItem.extractor_version != EXTRACTOR_VERSION)
            )
            | (
                (EvidenceItem.extracted_by == RELATIONSHIP_EXTRACTOR_NAME)
                & (EvidenceItem.extractor_version != RELATIONSHIP_EXTRACTOR_VERSION)
            )
        )
    ).all()

    if superseded_evidence:
        # A surviving edge — one a human curated — may cite evidence that is about to go.
        # It keeps its place in the graph but loses the citation, and re-extraction will
        # re-cite it if the corpus still supports it. There is no FK cascade here on
        # purpose: an edge must never disappear because a citation did.
        report.edges_uncited = len(
            session.scalars(
                select(EntityRelationship.id).where(
                    EntityRelationship.evidence_id.in_(superseded_evidence)
                )
            ).all()
        )
        session.execute(
            update(EntityRelationship)
            .where(EntityRelationship.evidence_id.in_(superseded_evidence))
            .values(evidence_id=None)
        )

    # --- 3. events built by a superseded extractor ---------------------------
    # Deleted before their evidence so that `event_evidence` empties by cascade from the
    # side that owns the row, and no event is left standing on withdrawn support.
    stale_events = session.scalars(
        select(Event).where(Event.extractor_version != EXTRACTOR_VERSION)
    ).all()
    for event in stale_events:
        session.delete(event)
    report.events_retracted = len(stale_events)
    session.flush()

    if superseded_evidence:
        # Findings resting entirely on withdrawn evidence are withdrawn with it. A finding
        # that keeps some support survives; the research loop will rebuild the rest.
        surviving = set(
            session.scalars(
                select(FindingEvidence.finding_id).where(
                    FindingEvidence.evidence_id.not_in(superseded_evidence)
                )
            ).all()
        )
        doomed = [
            finding_id
            for (finding_id,) in session.execute(
                select(FindingEvidence.finding_id)
                .where(FindingEvidence.evidence_id.in_(superseded_evidence))
                .distinct()
            ).all()
            if finding_id not in surviving
        ]
        if doomed:
            session.execute(
                delete(ResearchFinding).where(ResearchFinding.id.in_(doomed))
            )
            report.findings_retracted = len(doomed)

        session.execute(delete(EvidenceItem).where(EvidenceItem.id.in_(superseded_evidence)))
        report.evidence_retracted = len(superseded_evidence)

    session.flush()

    if report.anything_retracted:
        report.stale_reports = len(session.scalars(select(ResearchReport.id)).all())
        if report.stale_reports:
            report.notes.append(
                f"{report.stale_reports} research report(s) predate this retraction and may "
                "cite withdrawn evidence. Re-run `marketradar research` for each theme."
            )
        log.info(
            "retraction.complete",
            events=report.events_retracted,
            evidence=report.evidence_retracted,
            edges=report.edges_retracted,
            edges_uncited=report.edges_uncited,
            findings=report.findings_retracted,
            stale_reports=report.stale_reports,
        )
    return report


def rebuild_derived(session: Session) -> RetractionReport:
    """Withdraw ALL derived data, whatever version produced it.

    Version-gated retraction is the right default: it re-derives only what actually
    changed. But it can only withdraw what it recognises as superseded, and while the
    extractors are still moving, state accumulates in ways a version bump does not reach —
    a subject kept alive by evidence that keeps recreating it, a row from a version that no
    longer appears anywhere. Three runs in a row, a correct fix failed to reach stored data.

    This is the blunt instrument for that. It deletes every derived artefact and lets the
    next pass rebuild from documents, which are NOT touched: they are the expensive,
    rate-limited, non-derived part, and re-fetching them to rerun a regex would be absurd.
    Curated graph edges — those with no ``extractor_version`` — are also kept, because a
    human entered them and no extractor may withdraw them.
    """
    report = RetractionReport()

    for edge in session.scalars(
        select(EntityRelationship).where(EntityRelationship.extractor_version.is_not(None))
    ).all():
        session.delete(edge)
        report.edges_retracted += 1
    session.execute(
        update(EntityRelationship)
        .where(EntityRelationship.evidence_id.is_not(None))
        .values(evidence_id=None)
    )
    session.flush()

    # Ordered so a row is gone before anything that points at it.
    for model in (ThemeCompanyExposure, ThemeSignal, Trend, SignalObservation, Signal, Theme):
        for row in session.scalars(select(model)).all():
            session.delete(row)
        session.flush()

    for row in session.scalars(select(ResearchFinding)).all():
        session.delete(row)
        report.findings_retracted += 1
    for row in session.scalars(select(ResearchReport)).all():
        session.delete(row)
    session.flush()

    for row in session.scalars(select(Event)).all():
        session.delete(row)
        report.events_retracted += 1
    session.flush()

    for row in session.scalars(select(EvidenceItem)).all():
        session.delete(row)
        report.evidence_retracted += 1
    for row in session.scalars(select(Subject).where(Subject.is_discovered.is_(True))).all():
        session.delete(row)
    for row in session.scalars(select(Score)).all():
        session.delete(row)
    session.flush()

    report.notes.append(
        "Full rebuild: every derived artefact withdrawn. Documents, sources, companies and "
        "hand-curated graph edges were kept."
    )
    log.info(
        "retraction.rebuild",
        evidence=report.evidence_retracted,
        events=report.events_retracted,
        edges=report.edges_retracted,
    )
    return report


__all__ = ["RetractionReport", "rebuild_derived", "retract_superseded"]
