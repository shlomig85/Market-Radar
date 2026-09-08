"""Theme-level metrics and score assembly.

Collects the measurements a theme's scores depend on, then resolves each score component
either to a value with an explanation, or to an explicit ``unavailable`` with the reason.
Nothing is defaulted; the components this build cannot compute are named, and the score
model renormalises around them (ADR-007).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, EventType, SourceClass
from marketradar.domain.models import (
    Event,
    EventEvidence,
    EvidenceItem,
    Source,
    SourceDocument,
    Theme,
    ThemeCompanyExposure,
)
from marketradar.evidence.independence import (
    PRIMARY_CLASSES,
    EvidenceDescriptor,
    IndependenceProfile,
    profile,
)
from marketradar.scoring import (
    CONFIDENCE_V1,
    OPPORTUNITY_V1,
    TREND_V1,
    ComponentValue,
    ScoreResult,
    compute_score,
    unavailable,
    value,
)
from marketradar.signals.definitions import SIGNALS_BY_KEY
from marketradar.signals.engine import independence_factor
from marketradar.themes.formation import SignalActivation

METRICS_VERSION = "theme_metrics_v1"

#: Reason strings reused wherever a capability is missing, so the same gap always reads the
#: same way in the UI.
NO_MARKET_DATA = (
    "No market-data provider is configured, so price reaction and positioning cannot be "
    "measured. This component was excluded and the remaining weights renormalised."
)
NO_FUNDAMENTALS = (
    "No fundamental-data provider is configured, so revenue, margin and TAM effects cannot "
    "be estimated. This component was excluded and the remaining weights renormalised."
)
NO_CATALYSTS = (
    "The catalyst engine is not implemented in this build, so no forward events are "
    "scheduled or scored. This component was excluded and the remaining weights renormalised."
)
NO_RISK_ENGINE = (
    "The risk engine is not implemented in this build, so risk/reward is not scored. This "
    "component was excluded and the remaining weights renormalised."
)


@dataclass
class ThemeMetrics:
    """Everything measured about a theme in one place."""

    strength: float
    acceleration: float
    frequency_change: float
    independence: IndependenceProfile
    supporting_clusters: int
    contradicting_clusters: int
    mainstream_share: float
    market_reaction_observed: bool
    exposure_top: float
    exposure_count: int
    data_mode: DataMode

    @property
    def contradiction_ratio(self) -> float:
        total = self.supporting_clusters + self.contradicting_clusters
        return round(self.contradicting_clusters / total, 4) if total else 0.0

    @property
    def novelty(self) -> float:
        """Coverage-derived novelty proxy in 0..100.

        Honest naming matters here: this measures how much of the independent evidence comes
        from mainstream outlets and whether price movement has been *reported*. It is not a
        measurement of positioning, estimate revisions or valuation, none of which are
        available without a market-data provider.
        """
        penalty = 0.65 * self.mainstream_share + (0.35 if self.market_reaction_observed else 0.0)
        return round(100.0 * max(0.0, 1.0 - penalty), 4)


def collect_metrics(
    session: Session, theme: Theme, activations: list[SignalActivation], as_of: datetime
) -> ThemeMetrics:
    """Measure a theme over its observation window."""
    subjects = set((theme.anchor_entities or {}).get("subjects", []))
    window_start = min(
        (a.trend.observation_start for a in activations), default=as_of
    )

    rows = session.execute(
        select(Event, EvidenceItem, Source)
        .join(EventEvidence, EventEvidence.event_id == Event.id)
        .join(EvidenceItem, EvidenceItem.id == EventEvidence.evidence_id)
        .join(SourceDocument, SourceDocument.id == EvidenceItem.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
        .where(Event.occurred_at >= window_start, Event.occurred_at <= as_of)
    ).all()

    # A cluster supports the theme if any of its events push the theme's signals up, and
    # contradicts it if any push them down. Both are counted per *cluster*, so repeated
    # reporting of one contradiction is one contradiction.
    supporting: set[str] = set()
    contradicting: set[str] = set()
    descriptors: list[EvidenceDescriptor] = []
    market_reaction = False
    modes: list[DataMode] = []

    sign_map: dict[EventType, float] = {}
    for activation in activations:
        definition = SIGNALS_BY_KEY.get(activation.signal.key)
        if definition is None:
            continue
        for event_type, weight in definition.event_weights.items():
            sign_map[event_type] = sign_map.get(event_type, 0.0) + weight

    for event, item, source in rows:
        if event.subject_key not in subjects or event.cluster_id is None:
            continue
        modes.append(event.data_mode)
        descriptors.append(
            EvidenceDescriptor(
                evidence_id=item.id,
                cluster_id=event.cluster_id,
                source_class=source.source_class,
                source_quality=source.base_quality,
                is_primary=source.source_class in PRIMARY_CLASSES,
            )
        )
        if event.event_type == EventType.MARKET_REACTION:
            market_reaction = True
            continue
        sign = sign_map.get(event.event_type, 0.0)
        if sign > 0:
            supporting.add(event.cluster_id)
        elif sign < 0:
            contradicting.add(event.cluster_id)

    independence = profile(descriptors)

    by_cluster_class: dict[str, SourceClass] = {}
    by_cluster_quality: dict[str, int] = {}
    for descriptor in descriptors:
        if descriptor.source_quality >= by_cluster_quality.get(descriptor.cluster_id, -1):
            by_cluster_quality[descriptor.cluster_id] = descriptor.source_quality
            by_cluster_class[descriptor.cluster_id] = descriptor.source_class
    mainstream = (
        sum(1 for c in by_cluster_class.values() if c == SourceClass.FINANCIAL_MEDIA)
        / len(by_cluster_class)
        if by_cluster_class
        else 0.0
    )

    exposures = session.scalars(
        select(ThemeCompanyExposure).where(ThemeCompanyExposure.theme_id == theme.id)
    ).all()

    total_weight = sum(a.weight for a in activations) or 1.0
    strength = sum(a.trend.observation_strength * a.weight for a in activations) / total_weight
    acceleration = sum(a.trend.acceleration * a.weight for a in activations) / total_weight
    frequency = sum(a.trend.frequency_change * a.weight for a in activations) / total_weight

    return ThemeMetrics(
        strength=round(strength, 4),
        acceleration=round(acceleration, 4),
        frequency_change=round(frequency, 4),
        independence=independence,
        supporting_clusters=len(supporting),
        contradicting_clusters=len(contradicting),
        mainstream_share=round(mainstream, 4),
        market_reaction_observed=market_reaction,
        exposure_top=max((e.exposure_score for e in exposures), default=0.0),
        exposure_count=len(exposures),
        data_mode=DataMode.weakest(modes) if modes else DataMode.UNAVAILABLE,
    )


# ---------------------------------------------------------------- scores
def _independence_normalised(count: int) -> float:
    """Saturating 0..100 from an independent-source count."""
    return round(100.0 * (1.0 - math.exp(-count / 4.0)), 4)


def trend_score(metrics: ThemeMetrics, confidence: float) -> ScoreResult:
    """Trend score: how strong, and how *changing*, the theme is."""
    components: dict[str, ComponentValue] = {
        "signal_strength": value(
            metrics.strength,
            metrics.strength,
            f"Independence-adjusted evidence strength of {metrics.strength:.0f}/100 across "
            f"{metrics.independence.independent_source_count} independent sources.",
            independent_sources=metrics.independence.independent_source_count,
        ),
        "signal_acceleration": value(
            metrics.acceleration,
            # -100..100 mapped onto 0..100: 0 acceleration reads as 50, not as "bad".
            (metrics.acceleration + 100.0) / 2.0,
            f"Observation window runs {metrics.acceleration:.0f} (on a -100..100 scale) "
            "against the signals' own historical baselines.",
            frequency_change=metrics.frequency_change,
        ),
        "evidence_diversity": value(
            metrics.independence.source_diversity,
            metrics.independence.source_diversity * 100.0,
            f"Independent evidence spans source classes with a normalised entropy of "
            f"{metrics.independence.source_diversity:.2f}; "
            f"{metrics.independence.primary_source_ratio:.0%} of it is primary.",
            primary_source_ratio=metrics.independence.primary_source_ratio,
        ),
        "economic_impact": unavailable(NO_FUNDAMENTALS),
        "novelty": value(
            metrics.novelty,
            metrics.novelty,
            f"{metrics.mainstream_share:.0%} of independent sources are mainstream financial "
            "media"
            + (
                " and the evidence reports that prices have already moved."
                if metrics.market_reaction_observed
                else " and no price reaction is reported in the evidence."
            )
            + " Coverage-derived proxy only: positioning and estimate revisions are not measured.",
            mainstream_share=metrics.mainstream_share,
        ),
        "market_mispricing": unavailable(NO_MARKET_DATA),
        "company_exposure": value(
            metrics.exposure_top,
            metrics.exposure_top,
            f"{metrics.exposure_count} companies mapped through the value chain; the "
            f"strongest exposure scores {metrics.exposure_top:.0f}/100.",
            exposure_count=metrics.exposure_count,
        ),
        "catalyst_proximity": unavailable(NO_CATALYSTS),
        "confidence": value(
            confidence, confidence, f"Confidence score of {confidence:.0f}/100."
        ),
    }
    return compute_score(TREND_V1, components)


def confidence_score(metrics: ThemeMetrics) -> ScoreResult:
    """Confidence: how well established the conclusion is, reported separately.

    Every quality-derived component is attenuated by the independence gate. Source quality,
    primacy, diversity and a clean contradiction record all describe how good the evidence
    would be *if* it were corroborated; none of them is a substitute for corroboration. The
    attenuation is applied to the component's normalised value and named in its explanation,
    so the score stays fully decomposable.
    """
    independence = metrics.independence
    gate = independence_factor(independence.independent_source_count)
    attenuation = (
        f" Attenuated by an independence factor of {gate:.2f} "
        f"({independence.independent_source_count} independent source(s))."
    )
    components: dict[str, ComponentValue] = {
        "independent_sources": value(
            independence.independent_source_count,
            _independence_normalised(independence.independent_source_count),
            f"{independence.independent_source_count} independent confirmations from "
            f"{independence.evidence_count} evidence items "
            f"(amplification ratio {independence.amplification_ratio:.2f}).",
            amplification_ratio=independence.amplification_ratio,
        ),
        "source_quality": value(
            independence.avg_source_quality,
            independence.avg_source_quality * gate,
            f"Average credibility of the representative source per independent cluster is "
            f"{independence.avg_source_quality:.0f}/100." + attenuation,
        ),
        "primary_ratio": value(
            independence.primary_source_ratio,
            independence.primary_source_ratio * 100.0 * gate,
            f"{independence.primary_source_ratio:.0%} of independent evidence comes from "
            "filings, issuers or official data." + attenuation,
        ),
        "source_diversity": value(
            independence.source_diversity,
            independence.source_diversity * 100.0 * gate,
            f"Normalised entropy across source classes is "
            f"{independence.source_diversity:.2f}." + attenuation,
        ),
        "contradiction_balance": value(
            metrics.contradiction_ratio,
            100.0 * (1.0 - metrics.contradiction_ratio) * gate,
            f"{metrics.contradicting_clusters} of "
            f"{metrics.supporting_clusters + metrics.contradicting_clusters} independent "
            "evidence clusters contradict the theme." + attenuation,
            supporting=metrics.supporting_clusters,
            contradicting=metrics.contradicting_clusters,
        ),
    }
    return compute_score(CONFIDENCE_V1, components)


def opportunity_score(metrics: ThemeMetrics, trend: ScoreResult) -> ScoreResult:
    """Research-opportunity quality. Explicitly not a price forecast."""
    independence = metrics.independence
    evidence_quality = round(
        0.5 * _independence_normalised(independence.independent_source_count)
        + 0.3 * independence.avg_source_quality
        + 0.2 * independence.primary_source_ratio * 100.0,
        4,
    )
    components: dict[str, ComponentValue] = {
        "trend_strength": value(
            metrics.strength, metrics.strength, f"Trend strength {metrics.strength:.0f}/100."
        ),
        "trend_acceleration": value(
            metrics.acceleration,
            (metrics.acceleration + 100.0) / 2.0,
            f"Acceleration {metrics.acceleration:.0f} against baseline.",
        ),
        "company_exposure": value(
            metrics.exposure_top,
            metrics.exposure_top,
            f"Strongest mapped company exposure is {metrics.exposure_top:.0f}/100 across "
            f"{metrics.exposure_count} companies.",
        ),
        "market_mispricing": unavailable(NO_MARKET_DATA),
        "evidence_quality": value(
            evidence_quality,
            evidence_quality,
            f"Composite of independence ({independence.independent_source_count} sources), "
            f"average source quality ({independence.avg_source_quality:.0f}) and primary "
            f"share ({independence.primary_source_ratio:.0%}).",
        ),
        "catalyst_strength": unavailable(NO_CATALYSTS),
        "risk_reward": unavailable(NO_RISK_ENGINE),
        "novelty": value(
            metrics.novelty, metrics.novelty, f"Novelty proxy {metrics.novelty:.0f}/100."
        ),
    }
    return compute_score(OPPORTUNITY_V1, components)
