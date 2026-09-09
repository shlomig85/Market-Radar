"""Rating a company on how strongly it is caught up in something that is changing.

The product's visible output: a ranked list of companies with a **0-10 trend rating**.

The rating is an aggregation, not a new measurement. Everything it uses was computed and
stored by an earlier stage with its own evidence trail — theme trend scores, exposure paths,
independent-cluster counts — so a rating can always be taken apart into the reasons for it.
Nothing here introduces a number that is not already backed by documents.

Two decisions worth stating, because both are places a trend product usually cheats:

* **The strongest theme decides the rating, not the sum of all themes.** Adding contributions
  would let a company exposed weakly to five unrelated themes outrank one at the centre of a
  real move, purely on breadth. Additional themes add a bounded bonus instead.
* **A 0-10 rating is a presentation scale, not extra precision.** It is the stored 0-100
  score divided by ten and rounded to one decimal. Reporting 7.4 instead of 74 does not make
  it more exact, and no component is measured finely enough to justify more digits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import DataMode, ExposureRole
from marketradar.domain.models import (
    Company,
    Score,
    Signal,
    Theme,
    ThemeCompanyExposure,
    Trend,
)
from marketradar.logging import get_logger
from marketradar.scoring.engine import (
    ComponentValue,
    ScoreResult,
    compute_score,
    persist_score,
    value,
)
from marketradar.scoring.models import COMPANY_TREND_V1
from marketradar.themes.formation import independent_sources_in_window

log = get_logger(__name__)

#: Each additional theme a company is exposed to adds this share of the base rating, capped.
#: Breadth is corroborating, not multiplying: being adjacent to three moves is a better story
#: than one, but not three times the story.
ADDITIONAL_THEME_BONUS = 0.06
MAX_THEME_BONUS = 0.18

#: Independent clusters at which corroboration is considered complete. Past this, more
#: reporting of the same thing stops being more evidence.
CORROBORATION_TARGET = 8.0

#: Roles whose exposure is not a straightforward positive. A competitor or substitute is
#: genuinely exposed to a theme, but a *rising* theme does not straightforwardly help them.
_HEADWIND_ROLES = frozenset({ExposureRole.COMPETITOR, ExposureRole.SUBSTITUTE, ExposureRole.LOSER})


@dataclass
class CompanyTrendRating:
    """One company's rating, with everything needed to justify it."""

    company_key: str
    company_name: str
    ticker: str | None
    #: 0-100, stored. ``rating`` is this on the reader's 0-10 scale.
    score: float
    theme_slug: str
    theme_name: str
    role: ExposureRole
    order_of_effect: int
    exposure_score: float
    independent_clusters: int
    theme_count: int
    data_mode: DataMode
    rationale: str
    #: The real computed result, kept rather than reconstructed: it carries the weight
    #: coverage and the list of components that were unavailable, which is what makes a
    #: rating auditable and what a rebuilt copy would quietly lose.
    result: ScoreResult | None = None

    @property
    def components(self) -> list[dict]:
        """The decomposition, flattened for the API and the CLI."""
        if self.result is None:
            return []
        return [
            {
                "key": component.key,
                "label": component.label,
                "available": component.available,
                "normalized": component.normalized,
                "effective_weight": component.effective_weight,
                "contribution": component.contribution,
                "explanation": component.explanation,
            }
            for component in self.result.components
        ]

    @property
    def rating(self) -> float:
        """The 0-10 figure shown to a reader. One decimal: the inputs justify no more."""
        return round(self.score / 10.0, 1)

    @property
    def direction(self) -> str:
        """Whether a rising theme is a tailwind or a headwind for this company.

        A competitor of a beneficiary is exposed to the same move and does not benefit from
        it. Presenting both as "trending" without the distinction would be the single most
        misleading thing this list could do.
        """
        return "headwind" if self.role in _HEADWIND_ROLES else "tailwind"


def _theme_scores(session: Session) -> dict[str, dict[str, float]]:
    """Latest stored score per theme, keyed by theme id then model name."""
    scores: dict[str, dict[str, float]] = {}
    for row in session.scalars(
        select(Score).where(Score.subject_type == "THEME").order_by(Score.computed_at)
    ).all():
        scores.setdefault(row.subject_id, {})[row.model_name] = row.value
    return scores


def _theme_acceleration(session: Session, theme: Theme) -> tuple[float, int]:
    """Strongest acceleration among a theme's signals, and its independent-cluster support.

    Independence is counted with the same helper theme formation uses, over the trend's own
    observation window — a second, divergent way of counting "independent sources" would let
    a company rating and the theme it rests on disagree about their shared evidence.
    """
    best = 0.0
    clusters = 0
    for link in theme.theme_signals:
        trend = session.scalar(
            select(Trend)
            .where(Trend.signal_id == link.signal_id)
            .order_by(Trend.computed_at.desc())
            .limit(1)
        )
        if trend is None:
            continue
        best = max(best, trend.acceleration)
        signal = session.get(Signal, link.signal_id)
        if signal is not None:
            clusters = max(
                clusters,
                independent_sources_in_window(
                    session, signal, trend.observation_start, trend.observation_end
                ),
            )
    return best, clusters


def rate_companies(
    session: Session, as_of: datetime, persist: bool = True
) -> list[CompanyTrendRating]:
    """Rate every company reachable from a theme, strongest first.

    A company with no exposure to any theme is absent rather than rated zero: "we have no
    evidence about this company" and "the evidence says nothing is happening" are different
    statements, and a zero would assert the second while meaning the first.
    """
    themes = {theme.id: theme for theme in session.scalars(select(Theme)).all()}
    if not themes:
        return []

    companies = {company.id: company for company in session.scalars(select(Company)).all()}
    theme_scores = _theme_scores(session)
    acceleration_cache: dict[str, tuple[float, int]] = {}

    by_company: dict[str, list[ThemeCompanyExposure]] = {}
    for exposure in session.scalars(select(ThemeCompanyExposure)).all():
        by_company.setdefault(exposure.company_id, []).append(exposure)

    ratings: list[CompanyTrendRating] = []
    for company_id, exposures in by_company.items():
        company = companies.get(company_id)
        if company is None:
            continue

        best_rating: CompanyTrendRating | None = None
        for exposure in exposures:
            theme = themes.get(exposure.theme_id)
            if theme is None:
                continue
            if theme.id not in acceleration_cache:
                acceleration_cache[theme.id] = _theme_acceleration(session, theme)
            acceleration, clusters = acceleration_cache[theme.id]
            trend_score = theme_scores.get(theme.id, {}).get("trend_score")
            if trend_score is None:
                continue

            values: dict[str, ComponentValue] = {
                "theme_trend": value(
                    trend_score,
                    trend_score,
                    f"Theme '{theme.name}' scores {trend_score:.0f}/100 on measured change.",
                    theme=theme.slug,
                ),
                "theme_acceleration": value(
                    acceleration,
                    # Acceleration is -100..100; only upward movement supports a trend
                    # rating, and a decelerating theme contributes zero rather than
                    # subtracting from an otherwise sound case.
                    max(0.0, acceleration),
                    f"Strongest signal is accelerating at {acceleration:.0f}/100.",
                    acceleration=acceleration,
                ),
                "exposure": value(
                    exposure.exposure_score,
                    exposure.exposure_score,
                    f"{exposure.role.value.replace('_', ' ').title()}, "
                    f"order {exposure.order_of_effect}: {exposure.rationale or 'anchored'}",
                    order_of_effect=exposure.order_of_effect,
                ),
                "corroboration": value(
                    clusters,
                    min(100.0, 100.0 * clusters / CORROBORATION_TARGET),
                    f"{clusters} independent evidence cluster(s) behind the theme.",
                    independent_clusters=clusters,
                ),
                # No market-data provider is configured, so price confirmation is not
                # computed and its weight is redistributed rather than guessed at.
                "price_confirmation": ComponentValue(
                    available=False,
                    raw=None,
                    normalized=None,
                    explanation=(
                        "No market-data provider is configured, so whether price action "
                        "agrees with the evidence is not known. This component is dropped "
                        "and its weight redistributed rather than estimated."
                    ),
                ),
            }

            result = compute_score(COMPANY_TREND_V1, values)
            if not result.is_computable:
                continue

            candidate = CompanyTrendRating(
                company_key=company.key,
                company_name=company.name,
                ticker=company.ticker,
                score=result.value,
                theme_slug=theme.slug,
                theme_name=theme.name,
                role=exposure.role,
                order_of_effect=exposure.order_of_effect,
                exposure_score=exposure.exposure_score,
                independent_clusters=clusters,
                theme_count=len(exposures),
                # Weakest-wins: a rating built on DEMO evidence is DEMO, however it is shown.
                data_mode=DataMode.weakest([theme.data_mode, exposure.data_mode]),
                rationale=(
                    f"{exposure.role.value.replace('_', ' ').title()} of "
                    f"'{theme.name}' at order {exposure.order_of_effect}."
                ),
                result=result,
            )
            if best_rating is None or candidate.score > best_rating.score:
                best_rating = candidate

        if best_rating is None:
            continue

        # Breadth is corroborating, not multiplying.
        extra = min(MAX_THEME_BONUS, ADDITIONAL_THEME_BONUS * (best_rating.theme_count - 1))
        best_rating.score = round(min(100.0, best_rating.score * (1.0 + extra)), 4)
        if best_rating.result is not None:
            # Keep the stored score and its decomposition in step; a rating whose parts do
            # not add up to its total is worse than no decomposition at all.
            best_rating.result.value = best_rating.score
        ratings.append(best_rating)

    ratings.sort(key=lambda r: -r.score)

    if persist:
        by_key = {company.key: company for company in companies.values()}
        for rating in ratings:
            if rating.result is None:  # pragma: no cover - defensive
                continue
            company = by_key[rating.company_key]
            persist_score(
                session,
                rating.result,
                subject_type="COMPANY",
                subject_id=company.id,
                data_mode=rating.data_mode,
                computed_at=as_of,
                notes=rating.rationale,
            )
        session.flush()

    log.info("company_trend.rated", companies=len(ratings), as_of=as_of.isoformat())
    return ratings


__all__ = ["CompanyTrendRating", "rate_companies"]
