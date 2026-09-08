"""Report assembly.

Reports are **assembled from stored findings**, not written by a model (ADR-009). Every
statement in a section carries a ``claim_type`` and the ids of the evidence rows behind it,
so "show me where this came from" resolves for every line the UI renders.

The report answers the four questions the Master Build Prompt calls the golden rule — what
changed, why now, what the market already knows, and what says we are wrong — and it says
plainly when it cannot answer one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import (
    ClaimType,
    DataMode,
    FindingStance,
    ResearchDimension,
)
from marketradar.domain.models import (
    Company,
    FindingEvidence,
    ResearchFinding,
    ResearchReport,
    ResearchRun,
    Score,
    Signal,
    Theme,
    ThemeCompanyExposure,
    ThemeSignal,
    Trend,
)
from marketradar.providers import ProviderRegistry

GENERATOR = "deterministic_assembler"
GENERATOR_VERSION = "1.0.0"


def _statement(
    text: str, claim_type: ClaimType, evidence_ids: list[str] | None = None, **extra: object
) -> dict:
    """One report line: a sentence, its epistemic status, and its evidence."""
    return {
        "text": text,
        "claim_type": claim_type.value,
        "evidence_ids": evidence_ids or [],
        **extra,
    }


def assemble_report(
    session: Session,
    theme: Theme,
    run: ResearchRun,
    as_of: datetime,
    registry: ProviderRegistry,
) -> ResearchReport:
    """Build and persist the research report for a completed run."""
    findings = session.scalars(
        select(ResearchFinding).where(ResearchFinding.research_run_id == run.id)
    ).all()
    evidence_links = session.scalars(
        select(FindingEvidence).where(
            FindingEvidence.finding_id.in_([f.id for f in findings] or [""])
        )
    ).all()
    evidence_by_finding: dict[str, list[str]] = {}
    for link in evidence_links:
        evidence_by_finding.setdefault(link.finding_id, []).append(link.evidence_id)

    scores = _latest_scores(session, theme.id)
    trends = _theme_trends(session, theme)
    exposures = session.scalars(
        select(ThemeCompanyExposure)
        .where(ThemeCompanyExposure.theme_id == theme.id)
        .order_by(ThemeCompanyExposure.exposure_score.desc())
    ).all()
    companies = {c.id: c for c in session.scalars(select(Company)).all()}

    supporting = [f for f in findings if f.stance == FindingStance.SUPPORTING]
    contradicting = [f for f in findings if f.stance == FindingStance.CONTRADICTING]
    neutral = [
        f
        for f in findings
        if f.stance == FindingStance.NEUTRAL and f.independent_source_count > 0
    ]
    empty = [
        f
        for f in findings
        if f.independent_source_count == 0 and not f.claim.startswith("No company")
    ]

    sections: dict = {
        "what_changed": _what_changed(trends, supporting, evidence_by_finding),
        "why_it_matters": _why_it_matters(theme, exposures, companies),
        "why_now": _why_now(trends, theme),
        "supporting_evidence": [
            _statement(f.claim, f.claim_type, evidence_by_finding.get(f.id, []),
                       dimension=f.dimension.value, confidence=f.confidence,
                       independent_sources=f.independent_source_count,
                       reasoning=f.reasoning_summary)
            for f in sorted(supporting, key=lambda f: -f.independent_source_count)
        ],
        "contradictory_evidence": [
            _statement(f.claim, f.claim_type, evidence_by_finding.get(f.id, []),
                       dimension=f.dimension.value, confidence=f.confidence,
                       independent_sources=f.independent_source_count,
                       reasoning=f.reasoning_summary)
            for f in sorted(contradicting, key=lambda f: -f.independent_source_count)
        ],
        "contextual_findings": [
            _statement(f.claim, f.claim_type, evidence_by_finding.get(f.id, []),
                       dimension=f.dimension.value, confidence=f.confidence,
                       independent_sources=f.independent_source_count,
                       reasoning=f.reasoning_summary)
            for f in sorted(neutral, key=lambda f: -f.independent_source_count)
        ],
        "companies_exposed": _companies(exposures, companies),
        "market_awareness": _market_awareness(theme, findings, registry),
        "invalidation_conditions": _invalidation(theme, trends),
        "research_gaps": [
            _statement(f.claim, ClaimType.FACT, [], dimension=f.dimension.value)
            for f in empty
        ],
        "scores": {
            name: {
                "value": score.value,
                "model_version": score.model_version,
                "weight_coverage": score.weight_coverage,
                "unavailable_components": (score.unavailable_components or {}).get("keys", []),
                "components": [
                    {
                        "key": c.key,
                        "label": c.label,
                        "available": c.available,
                        "normalized": c.normalized,
                        "effective_weight": c.effective_weight,
                        "contribution": c.contribution,
                        "explanation": c.explanation,
                    }
                    for c in score.components
                ],
            }
            for name, score in scores.items()
        },
        "provenance": {
            "generator": GENERATOR,
            "generator_version": GENERATOR_VERSION,
            "pipeline_version": run.pipeline_version,
            "data_mode": theme.data_mode.value,
            "as_of": as_of.isoformat(),
            "provider_modes": {
                health.capability.value: health.mode.value
                for health in registry.health_report()
            },
            "disclaimer": (
                "Research output. Not investment advice, and not a forecast of any "
                "security's price. Scores rank research priority, not expected return."
            ),
        },
    }

    report = session.scalar(
        select(ResearchReport).where(ResearchReport.research_run_id == run.id)
    )
    if report is None:
        report = ResearchReport(
            research_run_id=run.id,
            theme_id=theme.id,
            title=f"{theme.name} — research briefing",
            generated_at=as_of,
            sections=sections,
            generator=GENERATOR,
            generator_version=GENERATOR_VERSION,
            data_mode=theme.data_mode,
        )
        session.add(report)
    else:
        report.sections = sections
        report.generated_at = as_of
    for attribute, model in (
        ("opportunity_score_id", "opportunity_score"),
        ("confidence_score_id", "confidence_score"),
        ("trend_score_id", "trend_score"),
    ):
        score = scores.get(model)
        setattr(report, attribute, score.id if score else None)
    session.flush()
    return report


# ---------------------------------------------------------------- sections
def _what_changed(
    trends: list[tuple[Signal, Trend]],
    supporting: list[ResearchFinding],
    evidence_by_finding: dict[str, list[str]],
) -> list[dict]:
    lines: list[dict] = []
    for signal, trend in sorted(trends, key=lambda pair: -pair[1].acceleration):
        lines.append(
            _statement(
                f"{signal.name} is running at {trend.observation_strength:.0f}/100 over the "
                f"observation window against {trend.baseline_strength:.0f}/100 in its prior "
                f"baseline (acceleration {trend.acceleration:+.0f} on a -100..100 scale).",
                # A measurement of our own corpus is a fact about the corpus.
                ClaimType.FACT,
                [],
                signal=signal.key,
                acceleration=trend.acceleration,
            )
        )
    strongest = max(supporting, key=lambda f: f.independent_source_count, default=None)
    if strongest is not None:
        lines.append(
            _statement(
                f"Strongest corroboration: {strongest.claim}",
                strongest.claim_type,
                evidence_by_finding.get(strongest.id, []),
            )
        )
    return lines


def _why_it_matters(
    theme: Theme, exposures: Sequence[ThemeCompanyExposure], companies: dict[str, Company]
) -> list[dict]:
    if not exposures:
        return [
            _statement(
                "No company exposure could be established, so the economic consequence of "
                "this change is not yet attributable to any issuer.",
                ClaimType.FACT,
            )
        ]
    first = [e for e in exposures if e.order_of_effect == 1]
    later = [e for e in exposures if e.order_of_effect > 1]
    lines = [
        _statement(
            f"{len(first)} companies are exposed at first order and {len(later)} at second "
            "order or beyond, so the change has an identifiable transmission path into "
            "public equities.",
            ClaimType.INFERENCE,
        )
    ]
    if later:
        best = max(later, key=lambda e: e.exposure_score)
        company = companies.get(best.company_id)
        if company:
            lines.append(
                _statement(
                    f"The strongest indirect exposure is {company.name} "
                    f"({best.role.value.replace('_', ' ').lower()}, order "
                    f"{best.order_of_effect}) via: {best.rationale}",
                    ClaimType.INFERENCE,
                )
            )
    return lines


def _why_now(trends: list[tuple[Signal, Trend]], theme: Theme) -> list[dict]:
    if not trends:
        return [
            _statement(
                "No trend is attached to this theme, so 'why now' cannot be answered.",
                ClaimType.FACT,
            )
        ]
    signal, trend = max(trends, key=lambda pair: pair[1].acceleration)
    lines = [
        _statement(
            f"The evidence rate changed: {signal.name} events arrive at "
            f"{(trend.inputs or {}).get('observation_rate_per_day', 0):.2f}/day in the "
            f"observation window against "
            f"{(trend.inputs or {}).get('baseline_rate_per_day', 0):.2f}/day in the baseline.",
            ClaimType.FACT,
        ),
        _statement(
            f"Independent breadth changed: independence is up {trend.independence_change:+.0f} "
            "on the same comparison, so the change is being reported by more separate "
            "sources rather than repeated by the same one.",
            ClaimType.FACT,
        ),
        _statement(
            f"Maturity is classified {theme.maturity.value} by deterministic rules over "
            "those measurements.",
            ClaimType.INFERENCE,
        ),
    ]
    return lines


def _companies(
    exposures: Sequence[ThemeCompanyExposure], companies: dict[str, Company]
) -> list[dict]:
    rows = []
    for exposure in exposures:
        company = companies.get(exposure.company_id)
        if company is None:
            continue
        rows.append(
            {
                "key": company.key,
                "name": company.name,
                "ticker": company.ticker,
                "is_fictional": company.is_fictional,
                "role": exposure.role.value,
                "order_of_effect": exposure.order_of_effect,
                "exposure_score": exposure.exposure_score,
                "confidence": exposure.confidence,
                "path": exposure.rationale,
                "data_mode": exposure.data_mode.value,
            }
        )
    return rows


def _market_awareness(
    theme: Theme, findings: Sequence[ResearchFinding], registry: ProviderRegistry
) -> list[dict]:
    market_health = registry.market_data.health()
    lines = [
        _statement(
            f"Coverage-derived market awareness is estimated as "
            f"{theme.market_awareness.value}.",
            ClaimType.INFERENCE,
            [],
            data_mode=theme.market_awareness_mode.value,
        )
    ]
    if market_health.mode == DataMode.UNAVAILABLE:
        lines.append(
            _statement(
                "This estimate is derived only from the mix of source types in the evidence. "
                "No market-data provider is configured, so price reaction, valuation, "
                "positioning and estimate revisions have NOT been measured. "
                f"Provider status: {market_health.detail}",
                ClaimType.FACT,
                [],
                data_mode=DataMode.UNAVAILABLE.value,
            )
        )
    expectations = [
        f for f in findings if f.dimension == ResearchDimension.MARKET_EXPECTATIONS
    ]
    for finding in expectations:
        lines.append(
            _statement(finding.claim, finding.claim_type, [], confidence=finding.confidence)
        )
    return lines


def _invalidation(theme: Theme, trends: list[tuple[Signal, Trend]]) -> list[dict]:
    """Invalidation conditions expressed against measurable signals.

    Each is a monitoring rule a later cycle can evaluate automatically, not prose: it names
    the signal, the direction and the comparison that would break the thesis.
    """
    conditions: list[dict] = []
    for signal, trend in trends:
        conditions.append(
            {
                "signal": signal.key,
                "signal_name": signal.name,
                "condition": (
                    f"{signal.name} acceleration falls below 0 against its baseline "
                    f"(currently {trend.acceleration:+.0f})."
                ),
                "current_value": trend.acceleration,
                "threshold": 0.0,
                "claim_type": ClaimType.HYPOTHESIS.value,
            }
        )
    conditions.append(
        {
            "signal": "contradiction_ratio",
            "signal_name": "Contradiction ratio",
            "condition": (
                "Independent contradicting evidence rises above 60% of independent evidence, "
                "which the maturity classifier treats as invalidating."
            ),
            "current_value": None,
            "threshold": 0.6,
            "claim_type": ClaimType.HYPOTHESIS.value,
        }
    )
    return conditions


# ---------------------------------------------------------------- helpers
def _latest_scores(session: Session, theme_id: str) -> dict[str, Score]:
    rows = session.scalars(
        select(Score)
        .where(Score.subject_type == "THEME", Score.subject_id == theme_id)
        .order_by(Score.computed_at.desc())
    ).all()
    latest: dict[str, Score] = {}
    for score in rows:
        latest.setdefault(score.model_name, score)
    return latest


def _theme_trends(session: Session, theme: Theme) -> list[tuple[Signal, Trend]]:
    links = session.scalars(select(ThemeSignal).where(ThemeSignal.theme_id == theme.id)).all()
    signal_ids = [link.signal_id for link in links]
    if not signal_ids:
        return []
    signals = {
        s.id: s for s in session.scalars(select(Signal).where(Signal.id.in_(signal_ids))).all()
    }
    trends = session.scalars(
        select(Trend).where(Trend.theme_id == theme.id, Trend.signal_id.in_(signal_ids))
    ).all()
    return [(signals[t.signal_id], t) for t in trends if t.signal_id in signals]
