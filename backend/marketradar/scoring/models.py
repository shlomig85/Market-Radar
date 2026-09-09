"""Score model definitions.

Weights live here as versioned data, never scattered through call sites. Changing a weight
means publishing a new ``version``, and every score row records the version that produced
it — so a historical score stays reproducible after the model moves on.

The weights follow the PRD's suggested models. They are starting points to be validated
against outcomes (``docs/evaluation-plan.md``), not claims of empirical calibration.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComponentSpec:
    key: str
    label: str
    weight: float
    description: str


@dataclass(frozen=True)
class ScoreModelSpec:
    name: str
    version: str
    description: str
    components: tuple[ComponentSpec, ...]

    def __post_init__(self) -> None:
        total = round(sum(c.weight for c in self.components), 6)
        if total != 1.0:
            raise ValueError(f"{self.name}: weights must sum to 1.0, got {total}")

    @property
    def component_keys(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.components)

    def component(self, key: str) -> ComponentSpec:
        for spec in self.components:
            if spec.key == key:
                return spec
        raise KeyError(key)


TREND_V1 = ScoreModelSpec(
    name="trend_score",
    version="1.0.0",
    description=(
        "How strong and how *changing* a theme is. Not a prediction about any security."
    ),
    components=(
        ComponentSpec("signal_strength", "Signal strength", 0.20,
                      "Independence-adjusted, quality-weighted strength of the evidence."),
        ComponentSpec("signal_acceleration", "Signal acceleration", 0.15,
                      "Observation window measured against the signal's own baseline."),
        ComponentSpec("evidence_diversity", "Evidence diversity", 0.15,
                      "Spread of independent evidence across source classes."),
        ComponentSpec("economic_impact", "Economic impact", 0.10,
                      "Size of the economic effect. Requires fundamental data."),
        ComponentSpec("novelty", "Novelty", 0.10,
                      "How far this is from being obvious, from coverage mix."),
        ComponentSpec("market_mispricing", "Market mispricing potential", 0.10,
                      "Gap between evidence and price reaction. Requires market data."),
        ComponentSpec("company_exposure", "Company exposure", 0.10,
                      "Strength of the mapping from the theme to identifiable companies."),
        ComponentSpec("catalyst_proximity", "Catalyst proximity", 0.05,
                      "Nearness of events that would validate or invalidate the thesis."),
        ComponentSpec("confidence", "Confidence", 0.05,
                      "How well established the underlying conclusion is."),
    ),
)

OPPORTUNITY_V1 = ScoreModelSpec(
    name="opportunity_score",
    version="1.0.0",
    description=(
        "Quality and attractiveness of the RESEARCH opportunity. This is explicitly NOT a "
        "probability that a security's price will rise."
    ),
    components=(
        ComponentSpec("trend_strength", "Trend strength", 0.25,
                      "Strength of the underlying measured change."),
        ComponentSpec("trend_acceleration", "Trend acceleration", 0.15,
                      "Rate of change against the baseline."),
        ComponentSpec("company_exposure", "Company exposure", 0.15,
                      "Whether identifiable public companies are meaningfully exposed."),
        ComponentSpec("market_mispricing", "Market mispricing potential", 0.15,
                      "Evidence strength relative to price reaction. Requires market data."),
        ComponentSpec("evidence_quality", "Evidence quality", 0.10,
                      "Independence, primacy and source quality of the evidence base."),
        ComponentSpec("catalyst_strength", "Catalyst strength", 0.10,
                      "Probability and impact of upcoming catalysts."),
        ComponentSpec("risk_reward", "Risk / reward", 0.05,
                      "Balance of identified risks against the opportunity."),
        ComponentSpec("novelty", "Novelty", 0.05,
                      "How far this is from consensus."),
    ),
)

CONFIDENCE_V1 = ScoreModelSpec(
    name="confidence_score",
    version="1.0.0",
    description=(
        "How confident we are that the conclusion is correct — deliberately separate from "
        "opportunity. High opportunity with low confidence is a real and useful state."
    ),
    components=(
        ComponentSpec("independent_sources", "Independent sources", 0.30,
                      "Number of genuinely independent confirmations."),
        ComponentSpec("source_quality", "Source quality", 0.20,
                      "Credibility of the sources behind the evidence."),
        ComponentSpec("primary_ratio", "Primary source ratio", 0.20,
                      "Share of evidence from filings, issuers or official data."),
        ComponentSpec("source_diversity", "Source diversity", 0.15,
                      "Spread across independent source classes."),
        ComponentSpec("contradiction_balance", "Contradiction balance", 0.15,
                      "Supporting versus contradicting evidence."),
    ),
)

ALL_MODELS: tuple[ScoreModelSpec, ...] = (TREND_V1, OPPORTUNITY_V1, CONFIDENCE_V1)


COMPANY_TREND_V1 = ScoreModelSpec(
    name="company_trend_score",
    version="1.0.0",
    description=(
        "How strongly a company is implicated in something that is measurably changing. "
        "Presented to a reader on a 0-10 scale; stored 0-100 like every other score so the "
        "components remain comparable across models."
    ),
    components=(
        ComponentSpec("theme_trend", "Theme trend strength", 0.35,
                      "Trend score of the strongest theme the company is exposed to."),
        ComponentSpec("theme_acceleration", "Theme acceleration", 0.20,
                      "How fast that theme is moving against its own baseline."),
        ComponentSpec("exposure", "Company exposure", 0.25,
                      "Strength of the causal path from the theme to this company."),
        ComponentSpec("corroboration", "Independent corroboration", 0.15,
                      "Independent evidence clusters behind the theme, not article count."),
        ComponentSpec("price_confirmation", "Price confirmation", 0.05,
                      "Whether price action agrees. Requires market data."),
    ),
)

