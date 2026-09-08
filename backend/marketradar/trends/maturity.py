"""Trend maturity classification.

Rules over measurable inputs, evaluated in order. An LLM does not assign maturity: the
stage drives what the product recommends a user spend attention on, so it must be
reproducible and arguable rather than vibed.

The thresholds are declared here as data so they can be tuned, versioned and back-tested in
one place (see ``docs/evaluation-plan.md``).
"""

from __future__ import annotations

from dataclasses import dataclass

from marketradar.domain.enums import MarketAwareness, TrendMaturity

CLASSIFIER_VERSION = "maturity_v1"


@dataclass(frozen=True)
class MaturityInputs:
    """Everything the classifier is allowed to look at."""

    independent_source_count: int
    acceleration: float
    observation_strength: float
    #: Share of independent clusters coming from mainstream financial media. Proxy for
    #: how public the story already is.
    mainstream_coverage_share: float
    #: True when the evidence itself reports that securities prices have already moved.
    market_reaction_observed: bool
    contradiction_ratio: float


@dataclass(frozen=True)
class MaturityResult:
    maturity: TrendMaturity
    market_awareness: MarketAwareness
    rationale: str
    classifier_version: str = CLASSIFIER_VERSION


#: Thresholds, named so the rationale strings can quote them.
MIN_SOURCES_EMERGING = 2
MIN_SOURCES_DEVELOPING = 4
MIN_SOURCES_ACCELERATING = 5
ACCELERATION_ACCELERATING = 25.0
ACCELERATION_STALLED = 5.0
MAINSTREAM_CONSENSUS = 0.45
MAINSTREAM_CROWDED = 0.65
CONTRADICTION_INVALIDATING = 0.6


def classify(inputs: MaturityInputs) -> MaturityResult:
    """Assign a maturity stage and a market-awareness estimate."""
    awareness = _awareness(inputs)

    if (
        inputs.contradiction_ratio >= CONTRADICTION_INVALIDATING
        and inputs.independent_source_count >= MIN_SOURCES_DEVELOPING
    ):
        return MaturityResult(
            TrendMaturity.INVALIDATED,
            awareness,
            f"Contradictory evidence is {inputs.contradiction_ratio:.0%} of independent "
            f"evidence, at or above the {CONTRADICTION_INVALIDATING:.0%} invalidation "
            "threshold, with enough sources for that ratio to be meaningful.",
        )

    if inputs.independent_source_count < MIN_SOURCES_EMERGING:
        return MaturityResult(
            TrendMaturity.INVISIBLE,
            awareness,
            f"Only {inputs.independent_source_count} independent source(s); below the "
            f"{MIN_SOURCES_EMERGING} needed to treat this as more than a single report.",
        )

    if inputs.mainstream_coverage_share >= MAINSTREAM_CROWDED and inputs.market_reaction_observed:
        return MaturityResult(
            TrendMaturity.CROWDED,
            awareness,
            f"Mainstream coverage is {inputs.mainstream_coverage_share:.0%} of independent "
            "sources and the evidence itself reports that prices have already moved.",
        )

    if inputs.mainstream_coverage_share >= MAINSTREAM_CONSENSUS:
        return MaturityResult(
            TrendMaturity.CONSENSUS,
            awareness,
            f"Mainstream financial media account for {inputs.mainstream_coverage_share:.0%} "
            f"of independent sources, at or above the {MAINSTREAM_CONSENSUS:.0%} consensus "
            "threshold.",
        )

    if (
        inputs.acceleration >= ACCELERATION_ACCELERATING
        and inputs.independent_source_count >= MIN_SOURCES_ACCELERATING
    ):
        return MaturityResult(
            TrendMaturity.ACCELERATING,
            awareness,
            f"Acceleration is {inputs.acceleration:.0f} against the signal's own baseline "
            f"with {inputs.independent_source_count} independent sources — evidence is both "
            "strengthening and broadening.",
        )

    if inputs.independent_source_count >= MIN_SOURCES_DEVELOPING:
        return MaturityResult(
            TrendMaturity.DEVELOPING,
            awareness,
            f"{inputs.independent_source_count} independent sources corroborate the change, "
            f"but acceleration ({inputs.acceleration:.0f}) is below the "
            f"{ACCELERATION_ACCELERATING:.0f} threshold for an accelerating stage.",
        )

    if inputs.acceleration <= ACCELERATION_STALLED:
        return MaturityResult(
            TrendMaturity.MATURE,
            awareness,
            f"Evidence exists but acceleration ({inputs.acceleration:.0f}) is at or below "
            f"{ACCELERATION_STALLED:.0f}: the phenomenon is no longer changing materially.",
        )

    return MaturityResult(
        TrendMaturity.EMERGING,
        awareness,
        f"{inputs.independent_source_count} independent sources with acceleration "
        f"{inputs.acceleration:.0f}: a real change, not yet broadly corroborated.",
    )


def _awareness(inputs: MaturityInputs) -> MarketAwareness:
    """Coverage-derived awareness estimate.

    With no market-data provider configured this is a **proxy** built from coverage mix and
    reported price reaction, not a measurement of positioning. Callers record its data mode
    separately so the UI can say so.
    """
    if inputs.mainstream_coverage_share >= MAINSTREAM_CROWDED and inputs.market_reaction_observed:
        return MarketAwareness.CROWDED
    if inputs.mainstream_coverage_share >= MAINSTREAM_CONSENSUS:
        return MarketAwareness.CONSENSUS
    if inputs.mainstream_coverage_share >= 0.25 or inputs.market_reaction_observed:
        return MarketAwareness.DEVELOPING
    if inputs.mainstream_coverage_share > 0.0:
        return MarketAwareness.EMERGING
    return MarketAwareness.UNKNOWN
