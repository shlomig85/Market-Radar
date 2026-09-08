"""Signal computation.

Turns events into a bounded 0-100 strength per time bucket. Three properties matter:

1. **Independence-adjusted.** Contributions are aggregated per evidence cluster, taking the
   strongest contribution within each cluster. Five rewrites of one announcement therefore
   contribute once. This is the arithmetic that makes amplification worthless.
2. **Quality-weighted.** A regulatory filing counts for more than a forum post, using the
   source's credibility prior.
3. **Saturating.** Strength approaches 100 asymptotically, so a very large evidence pile
   cannot dominate a comparison purely by volume.

Every input to the calculation is stored on the observation row, so any number the UI shows
can be recomputed by hand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, Direction, SourceClass
from marketradar.domain.models import (
    Event,
    EventEvidence,
    EvidenceItem,
    Signal,
    SignalObservation,
    Source,
    SourceDocument,
)
from marketradar.evidence.independence import (
    PRIMARY_CLASSES,
    EvidenceDescriptor,
    IndependenceProfile,
    profile,
)
from marketradar.logging import get_logger
from marketradar.signals.definitions import SignalDefinition

log = get_logger(__name__)

COMPUTATION_VERSION = "signal_v1"

#: Controls how quickly strength saturates. At an independence-adjusted contribution of
#: SATURATION_K the signal reads ~63/100; at 3x it reads ~95/100.
SATURATION_K = 2.5

#: Bucket width. Seven days smooths reporting-day clustering without blurring a month.
BUCKET_DAYS = 7

#: Days of history computed, covering baseline + observation windows with room to spare.
HISTORY_DAYS = 126


@dataclass(frozen=True)
class EventContribution:
    """One event's contribution to a signal, with the numbers that produced it."""

    event_id: str
    cluster_id: str
    event_type: str
    sign: float
    magnitude: float
    confidence: float
    quality_factor: float

    @property
    def value(self) -> float:
        """Unsigned strength contribution."""
        return self.magnitude * self.confidence * self.quality_factor

    @property
    def signed_value(self) -> float:
        return self.sign * self.value


@dataclass(frozen=True)
class BucketResult:
    """A computed signal value for one bucket, with full inputs for explainability."""

    bucket_start: datetime
    bucket_end: datetime
    strength: float
    confidence: float
    direction: Direction
    net_contribution: float
    event_count: int
    independence: IndependenceProfile
    contributions: list[EventContribution]
    data_mode: DataMode


def saturating_strength(net_contribution: float, k: float = SATURATION_K) -> float:
    """Map an unbounded contribution to 0..100 with diminishing returns."""
    return round(100.0 * (1.0 - math.exp(-abs(net_contribution) / k)), 4)


def independence_factor(independent_source_count: int) -> float:
    """Saturating 0..1 gate derived from the number of independent sources.

    Used to attenuate every quality-based term in a confidence calculation. Source quality
    and primacy describe how good the evidence is *if* it is right; they cannot stand in for
    corroboration. Without this gate a single regulatory filing scored above 50/100, which
    inverts the scepticism the product is built on.
    """
    if independent_source_count <= 0:
        return 0.0
    return 1.0 - math.exp(-independent_source_count / 2.5)


def confidence_from_independence(profile_: IndependenceProfile) -> float:
    """Confidence in 0..100 from independence, diversity, primacy and source quality.

    Structure: ``independence x quality``, not ``independence + quality``. A single
    independent source therefore cannot exceed roughly 33 however authoritative it is,
    because one source is one source.
    """
    if profile_.independent_source_count == 0:
        return 0.0
    gate = independence_factor(profile_.independent_source_count)
    quality = (
        0.30 * profile_.source_diversity
        + 0.35 * profile_.primary_source_ratio
        + 0.35 * (profile_.avg_source_quality / 100.0)
    )
    # The 0.45 floor keeps a well-corroborated but lower-quality evidence base from
    # collapsing to nothing: breadth has value even when every source is mediocre.
    value = gate * (0.45 + 0.55 * quality)
    return round(min(100.0, max(0.0, value * 100.0)), 4)


class SignalEngine:
    """Computes and persists signal observations."""

    def __init__(self, session: Session, as_of: datetime) -> None:
        """``as_of`` is mandatory, not optional.

        Every event read is gated on ``knowable_at <= as_of``. Making this a required
        constructor argument means a caller cannot accidentally compute a signal over
        information that did not exist yet — the failure mode is a missing argument at
        import time rather than silently contaminated output.
        """
        self.session = session
        self.as_of = as_of
        self._evidence_index: dict[str, list[tuple[EvidenceItem, Source]]] | None = None

    # ------------------------------------------------------------------
    def _evidence_for_events(self) -> dict[str, list[tuple[EvidenceItem, Source]]]:
        """Map event id -> its evidence rows joined to their sources (loaded once)."""
        if self._evidence_index is not None:
            return self._evidence_index
        rows = self.session.execute(
            select(EventEvidence.event_id, EvidenceItem, Source)
            .join(EvidenceItem, EvidenceItem.id == EventEvidence.evidence_id)
            .join(SourceDocument, SourceDocument.id == EvidenceItem.document_id)
            .join(Source, Source.id == SourceDocument.source_id)
        ).all()
        index: dict[str, list[tuple[EvidenceItem, Source]]] = {}
        for event_id, item, source in rows:
            index.setdefault(event_id, []).append((item, source))
        self._evidence_index = index
        return index

    # ------------------------------------------------------------------
    def compute_bucket(
        self, definition: SignalDefinition, start: datetime, end: datetime
    ) -> BucketResult:
        """Compute one signal bucket from the events that occurred inside it."""
        events = self.session.scalars(
            select(Event).where(
                Event.subject_key == definition.subject_key,
                Event.occurred_at >= start,
                Event.occurred_at < end,
                # The temporal-integrity gate. Without it, a document published after the
                # as-of instant describing an earlier event contaminates a historical window.
                Event.knowable_at <= self.as_of,
            )
        ).all()

        evidence_index = self._evidence_for_events()
        contributions: list[EventContribution] = []
        descriptors: list[EvidenceDescriptor] = []
        modes: list[DataMode] = []

        for event in events:
            sign = definition.event_weights.get(event.event_type)
            if sign is None or event.cluster_id is None:
                continue
            supporting = evidence_index.get(event.id, [])
            if not supporting:
                continue
            best_quality = max(source.base_quality for _, source in supporting)
            contributions.append(
                EventContribution(
                    event_id=event.id,
                    cluster_id=event.cluster_id,
                    event_type=event.event_type.value,
                    sign=sign,
                    magnitude=event.magnitude,
                    confidence=event.confidence,
                    quality_factor=best_quality / 100.0,
                )
            )
            modes.append(event.data_mode)
            for item, source in supporting:
                descriptors.append(
                    EvidenceDescriptor(
                        evidence_id=item.id,
                        cluster_id=event.cluster_id,
                        source_class=source.source_class,
                        source_quality=source.base_quality,
                        is_primary=source.source_class in PRIMARY_CLASSES,
                    )
                )

        # Independence adjustment: one contribution per cluster — the strongest — so that
        # repeated reporting of a single announcement cannot compound.
        per_cluster: dict[str, EventContribution] = {}
        for contribution in contributions:
            current = per_cluster.get(contribution.cluster_id)
            if current is None or abs(contribution.signed_value) > abs(current.signed_value):
                per_cluster[contribution.cluster_id] = contribution

        net = sum(c.signed_value for c in per_cluster.values())
        independence = profile(descriptors)
        direction = (
            Direction.POSITIVE
            if net > 0.05
            else Direction.NEGATIVE
            if net < -0.05
            else Direction.NEUTRAL
        )
        return BucketResult(
            bucket_start=start,
            bucket_end=end,
            strength=saturating_strength(net),
            confidence=confidence_from_independence(independence),
            direction=direction,
            net_contribution=round(net, 6),
            event_count=len(contributions),
            independence=independence,
            contributions=contributions,
            data_mode=DataMode.weakest(modes) if modes else DataMode.UNAVAILABLE,
        )

    # ------------------------------------------------------------------
    def ensure_signal(self, definition: SignalDefinition) -> Signal:
        signal = self.session.scalar(select(Signal).where(Signal.key == definition.key))
        if signal is None:
            signal = Signal(
                key=definition.key,
                name=definition.name,
                category=definition.category,
                subject_key=definition.subject_key,
                description=definition.description,
                event_types={k.value: v for k, v in definition.event_weights.items()},
            )
            self.session.add(signal)
            self.session.flush()
        return signal

    def compute_signal(
        self,
        definition: SignalDefinition,
        as_of: datetime,
        history_days: int = HISTORY_DAYS,
        bucket_days: int = BUCKET_DAYS,
    ) -> list[SignalObservation]:
        """Compute and upsert every bucket for a signal over the history window."""
        signal = self.ensure_signal(definition)
        existing = {
            observation.bucket_start: observation
            for observation in self.session.scalars(
                select(SignalObservation).where(
                    SignalObservation.signal_id == signal.id,
                    SignalObservation.computation_version == COMPUTATION_VERSION,
                )
            ).all()
        }

        observations: list[SignalObservation] = []
        bucket_count = history_days // bucket_days
        for index in range(bucket_count):
            end = as_of - timedelta(days=bucket_days * index)
            start = end - timedelta(days=bucket_days)
            result = self.compute_bucket(definition, start, end)

            row = existing.get(start)
            if row is None:
                row = SignalObservation(
                    signal_id=signal.id,
                    bucket_start=start,
                    bucket_end=end,
                    computation_version=COMPUTATION_VERSION,
                    strength=0.0,
                    confidence=0.0,
                    net_direction=Direction.NEUTRAL,
                    data_mode=DataMode.UNAVAILABLE,
                    computed_at=as_of,
                )
                self.session.add(row)

            row.strength = result.strength
            row.confidence = result.confidence
            row.net_direction = result.direction
            row.event_count = result.event_count
            row.independent_source_count = result.independence.independent_source_count
            row.underlying_event_count = result.independence.underlying_event_count
            row.source_diversity = round(result.independence.source_diversity, 4)
            row.primary_source_ratio = round(result.independence.primary_source_ratio, 4)
            row.avg_source_quality = round(result.independence.avg_source_quality, 4)
            row.data_mode = result.data_mode
            row.computed_at = as_of
            row.inputs = {
                "net_contribution": result.net_contribution,
                "saturation_k": SATURATION_K,
                "evidence_count": result.independence.evidence_count,
                "amplification_ratio": round(result.independence.amplification_ratio, 4),
                "contributions": [
                    {
                        "event_id": c.event_id,
                        "cluster_id": c.cluster_id,
                        "event_type": c.event_type,
                        "sign": c.sign,
                        "magnitude": c.magnitude,
                        "confidence": c.confidence,
                        "quality_factor": round(c.quality_factor, 4),
                        "signed_value": round(c.signed_value, 6),
                    }
                    for c in result.contributions
                ],
            }
            observations.append(row)

        self.session.flush()
        return observations


def mainstream_share(descriptors: list[EvidenceDescriptor]) -> float:
    """Share of independent clusters whose representative source is mainstream media.

    Used as the coverage-based novelty proxy. It is a proxy, not a measurement of market
    awareness, and everywhere it is consumed it is labelled as one.
    """
    if not descriptors:
        return 0.0
    by_cluster: dict[str, SourceClass] = {}
    quality: dict[str, int] = {}
    for descriptor in descriptors:
        if descriptor.source_quality >= quality.get(descriptor.cluster_id, -1):
            quality[descriptor.cluster_id] = descriptor.source_quality
            by_cluster[descriptor.cluster_id] = descriptor.source_class
    mainstream = sum(
        1 for cls in by_cluster.values() if cls in {SourceClass.FINANCIAL_MEDIA}
    )
    return mainstream / len(by_cluster)
