"""Finding construction.

A finding is the structured answer to one research question. It carries no free-floating
facts: its content is the evidence rows it links to, plus counts and a claim type stating
whether the sentence is an observation, an inference, a hypothesis or a forecast.

"We searched and found nothing" is a first-class result. A dimension with no evidence
produces a finding that says so, because a silent gap in the research is
indistinguishable from a gap in the world — and the first is our problem, not the market's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import (
    ClaimType,
    DataMode,
    EventType,
    FindingStance,
    ResearchDimension,
)
from marketradar.domain.models import (
    EvidenceItem,
    Source,
    SourceDocument,
    ThemeCompanyExposure,
)
from marketradar.evidence.independence import (
    PRIMARY_CLASSES,
    EvidenceDescriptor,
    profile,
)

FINDINGS_VERSION = "findings_v1"

#: Which observed event types answer which research dimension.
DIMENSION_EVENT_TYPES: dict[ResearchDimension, frozenset[EventType]] = {
    ResearchDimension.DEMAND: frozenset(
        {EventType.DEMAND_ACCELERATION, EventType.DEMAND_WEAKNESS, EventType.CONTRACT_AWARD}
    ),
    ResearchDimension.SUPPLY: frozenset(
        {
            EventType.SUPPLY_CONSTRAINT,
            EventType.SUPPLY_EXPANSION,
            EventType.INVENTORY_DECLINE,
            EventType.INVENTORY_BUILD,
        }
    ),
    ResearchDimension.PRICING: frozenset(
        {EventType.PRICING_INCREASE, EventType.PRICING_DECREASE}
    ),
    ResearchDimension.CAPACITY: frozenset(
        {EventType.CAPACITY_EXPANSION, EventType.CAPEX_INCREASE, EventType.CAPEX_DECREASE}
    ),
    ResearchDimension.CUSTOMERS: frozenset(
        {EventType.DEMAND_ACCELERATION, EventType.CONTRACT_AWARD}
    ),
    ResearchDimension.COMPETITION: frozenset(
        {EventType.CAPACITY_EXPANSION, EventType.PRODUCT_LAUNCH, EventType.SUPPLY_EXPANSION}
    ),
    ResearchDimension.TECHNOLOGY: frozenset(
        {EventType.TECHNOLOGY_ADOPTION, EventType.PRODUCT_LAUNCH}
    ),
    ResearchDimension.MARKET_EXPECTATIONS: frozenset({EventType.MARKET_REACTION}),
    ResearchDimension.RISKS: frozenset(
        {
            EventType.DEMAND_WEAKNESS,
            EventType.PRICING_DECREASE,
            EventType.INVENTORY_BUILD,
            EventType.SUPPLY_EXPANSION,
            EventType.CAPACITY_EXPANSION,
        }
    ),
    ResearchDimension.CONTRADICTORY_EVIDENCE: frozenset(
        {
            EventType.DEMAND_WEAKNESS,
            EventType.PRICING_DECREASE,
            EventType.INVENTORY_BUILD,
            EventType.SUPPLY_EXPANSION,
        }
    ),
}

#: Dimensions answered by forward-looking / historical statements rather than observations.
SPECULATIVE_DIMENSIONS = frozenset(
    {ResearchDimension.HISTORICAL_ANALOGUE, ResearchDimension.CONTRADICTORY_EVIDENCE,
     ResearchDimension.RISKS}
)

#: Dimensions whose stance is contradicting whatever the evidence sign says.
COUNTER_DIMENSIONS = frozenset(
    {ResearchDimension.CONTRADICTORY_EVIDENCE, ResearchDimension.RISKS}
)

#: Dimensions that inform the thesis without arguing for or against it. Reported price
#: movement, for instance, tells us what the market already knows — it is neither support
#: nor contradiction, and filing it as either would misstate the balance of evidence.
NEUTRAL_DIMENSIONS = frozenset(
    {ResearchDimension.MARKET_EXPECTATIONS, ResearchDimension.HISTORICAL_ANALOGUE}
)


@dataclass
class FindingDraft:
    """A finding before persistence."""

    dimension: ResearchDimension
    claim: str
    claim_type: ClaimType
    stance: FindingStance
    confidence: float
    independent_source_count: int
    source_diversity: float
    reasoning_summary: str
    evidence_ids: list[str]
    data_mode: DataMode


def gather_evidence(
    session: Session,
    dimension: ResearchDimension,
    subjects: set[str],
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[EvidenceItem, Source]]:
    """Evidence relevant to a dimension within the window."""
    rows = session.execute(
        select(EvidenceItem, Source)
        .join(SourceDocument, SourceDocument.id == EvidenceItem.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
        .where(
            EvidenceItem.event_at >= window_start,
            EvidenceItem.event_at <= window_end,
        )
    ).all()

    wanted = DIMENSION_EVENT_TYPES.get(dimension, frozenset())
    selected: list[tuple[EvidenceItem, Source]] = []
    for item, source in rows:
        if item.subject_key not in subjects:
            continue
        if item.event_type is None:
            # Forward-looking and historical statements answer risk, contradiction and
            # analogue questions, and nothing else.
            if dimension in SPECULATIVE_DIMENSIONS:
                selected.append((item, source))
            continue
        if item.event_type in wanted:
            selected.append((item, source))
    return selected


def build_finding(
    dimension: ResearchDimension,
    evidence: list[tuple[EvidenceItem, Source]],
    supporting_types: frozenset[EventType],
) -> FindingDraft:
    """Assemble a finding from evidence, or record that none was found."""
    if not evidence:
        return FindingDraft(
            dimension=dimension,
            claim=(
                f"No evidence addressing {dimension.value.replace('_', ' ').lower()} was "
                "located in the available corpus for this window."
            ),
            claim_type=ClaimType.FACT,
            stance=FindingStance.NEUTRAL,
            confidence=0.0,
            independent_source_count=0,
            source_diversity=0.0,
            reasoning_summary=(
                "This is a statement about the search, not about the world: the configured "
                "providers returned nothing relevant. A different provider set could change "
                "this answer."
            ),
            evidence_ids=[],
            data_mode=DataMode.UNAVAILABLE,
        )

    descriptors = [
        EvidenceDescriptor(
            evidence_id=item.id,
            cluster_id=item.cluster_id or item.id,
            source_class=source.source_class,
            source_quality=source.base_quality,
            is_primary=source.source_class in PRIMARY_CLASSES,
        )
        for item, source in evidence
    ]
    independence = profile(descriptors)

    speculative = [item for item, _ in evidence if item.event_type is None]
    observed = [item for item, _ in evidence if item.event_type is not None]

    by_type: dict[str, int] = {}
    for item in observed:
        assert item.event_type is not None
        by_type[item.event_type.value] = by_type.get(item.event_type.value, 0) + 1

    if dimension in COUNTER_DIMENSIONS:
        stance = FindingStance.CONTRADICTING
    elif dimension in NEUTRAL_DIMENSIONS:
        stance = FindingStance.NEUTRAL
    else:
        supports = sum(1 for i in observed if i.event_type in supporting_types)
        opposes = len(observed) - supports
        stance = (
            FindingStance.SUPPORTING
            if supports > opposes
            else FindingStance.CONTRADICTING
            if opposes > supports
            else FindingStance.NEUTRAL
        )

    # A finding built purely from projections and historical analogies is a hypothesis, not
    # a fact — and is labelled that way even when it is the most interesting thing found.
    if not observed:
        claim_type = ClaimType.HYPOTHESIS
    elif len(by_type) > 1:
        claim_type = ClaimType.INFERENCE
    else:
        claim_type = ClaimType.FACT

    breakdown = ", ".join(
        f"{count}x {name.replace('_', ' ').lower()}" for name, count in sorted(by_type.items())
    )
    descriptor = dimension.value.replace("_", " ").lower()
    if observed and speculative:
        claim = (
            f"{independence.independent_source_count} independent source(s) on {descriptor}: "
            f"{breakdown}; plus {len(speculative)} forward-looking or historical statement(s)."
        )
    elif observed:
        claim = (
            f"{independence.independent_source_count} independent source(s) on {descriptor}: "
            f"{breakdown}."
        )
    else:
        claim = (
            f"{len(speculative)} forward-looking or historical statement(s) bear on "
            f"{descriptor}; no observed event of this kind was recorded."
        )

    # Confidence is capped by independence: however many documents were read, a single
    # underlying source is a single underlying source.
    confidence = min(
        1.0,
        0.25 * min(independence.independent_source_count, 4)
        + 0.3 * (independence.avg_source_quality / 100.0)
        * (1.0 if independence.independent_source_count > 1 else 0.5),
    )

    return FindingDraft(
        dimension=dimension,
        claim=claim,
        claim_type=claim_type,
        stance=stance,
        confidence=round(confidence, 4),
        independent_source_count=independence.independent_source_count,
        source_diversity=round(independence.source_diversity, 4),
        reasoning_summary=(
            f"{independence.evidence_count} evidence items collapse to "
            f"{independence.independent_source_count} independent source(s) "
            f"(amplification ratio {independence.amplification_ratio:.2f}); "
            f"{independence.primary_source_ratio:.0%} primary; "
            f"average source quality {independence.avg_source_quality:.0f}/100."
        ),
        evidence_ids=[item.id for item, _ in evidence],
        data_mode=DataMode.weakest([item.data_mode for item, _ in evidence]),
    )


def build_exposure_finding(
    exposures: list[ThemeCompanyExposure], company_names: dict[str, str]
) -> FindingDraft:
    """Company-exposure finding, grounded in graph paths rather than document evidence."""
    if not exposures:
        return FindingDraft(
            dimension=ResearchDimension.COMPANY_EXPOSURE,
            claim="No company could be mapped to this theme through the knowledge graph.",
            claim_type=ClaimType.FACT,
            stance=FindingStance.NEUTRAL,
            confidence=0.0,
            independent_source_count=0,
            source_diversity=0.0,
            reasoning_summary="The value-chain traversal returned no company within its hop limit.",
            evidence_ids=[],
            data_mode=DataMode.UNAVAILABLE,
        )

    ranked = sorted(exposures, key=lambda e: -e.exposure_score)[:5]
    listed = "; ".join(
        f"{company_names.get(e.company_id, e.company_id)} "
        f"({e.role.value.replace('_', ' ').lower()}, order {e.order_of_effect}, "
        f"exposure {e.exposure_score:.0f})"
        for e in ranked
    )
    return FindingDraft(
        dimension=ResearchDimension.COMPANY_EXPOSURE,
        claim=f"{len(exposures)} companies map to this theme through the value chain: {listed}.",
        # An inference: the graph edges are evidence about relationships, and the exposure
        # ranking is derived from them rather than stated by any source.
        claim_type=ClaimType.INFERENCE,
        stance=FindingStance.SUPPORTING,
        confidence=round(min(1.0, max(e.confidence for e in ranked) / 100.0), 4),
        independent_source_count=len(ranked),
        source_diversity=0.0,
        reasoning_summary=(
            "Derived from knowledge-graph traversal, not from document evidence. Each "
            "exposure stores the exact hops, edge weights and edge confidences that produced "
            "it. Paths: "
            + " | ".join(f"{company_names.get(e.company_id, '?')}: {e.rationale}" for e in ranked)
        ),
        evidence_ids=[],
        data_mode=DataMode.weakest([e.data_mode for e in ranked]),
    )
