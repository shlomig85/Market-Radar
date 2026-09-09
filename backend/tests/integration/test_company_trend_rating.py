"""The product's visible output: companies ranked 0-10 by trend involvement.

A rating introduces no new measurement — it aggregates theme trend, exposure and
independent corroboration, each already stored with its own evidence trail. These tests
cover the ways such a list misleads: implying precision it does not have, presenting a
company a trend works *against* as if it benefits, and asserting "nothing is happening"
when the truth is "we have no evidence".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.domain.enums import (
    DataMode,
    ExposureRole,
    MarketAwareness,
    TrendMaturity,
)
from marketradar.domain.models import Company, Score, Theme, ThemeCompanyExposure
from marketradar.scoring.company_trend import MAX_THEME_BONUS, rate_companies
from marketradar.scoring.engine import ComponentValue, compute_score, persist_score, value
from marketradar.scoring.models import TREND_V1

AS_OF = datetime(2026, 9, 9, tzinfo=UTC)


def _theme(session: Session, slug: str, trend_value: float) -> Theme:
    theme = Theme(
        slug=slug,
        name=slug.replace("-", " ").title(),
        maturity=TrendMaturity.EMERGING,
        market_awareness=MarketAwareness.UNKNOWN,
        first_detected_at=AS_OF,
        last_updated_at=AS_OF,
        data_mode=DataMode.LIVE,
    )
    session.add(theme)
    session.flush()

    values: dict[str, ComponentValue] = {}
    for spec in TREND_V1.components:
        values[spec.key] = value(trend_value, trend_value, "test input")
    persist_score(
        session,
        compute_score(TREND_V1, values),
        subject_type="THEME",
        subject_id=theme.id,
        data_mode=DataMode.LIVE,
        computed_at=AS_OF,
    )
    session.flush()
    return theme


def _company(session: Session, key: str, ticker: str) -> Company:
    company = Company(
        key=key, name=key.title(), ticker=ticker, is_fictional=False, data_mode=DataMode.LIVE
    )
    session.add(company)
    session.flush()
    return company


def _expose(
    session: Session,
    theme: Theme,
    company: Company,
    score: float,
    order: int = 1,
    role: ExposureRole = ExposureRole.DIRECT_BENEFICIARY,
    mode: DataMode = DataMode.LIVE,
) -> ThemeCompanyExposure:
    exposure = ThemeCompanyExposure(
        theme_id=theme.id,
        company_id=company.id,
        computation_version="test",
        role=role,
        order_of_effect=order,
        exposure_score=score,
        confidence=80.0,
        data_mode=mode,
        computed_at=AS_OF,
        rationale="test path",
        path={"hops": [], "anchored_by": "evidence"},
    )
    session.add(exposure)
    session.flush()
    return exposure


@pytest.fixture
def universe(session: Session) -> Theme:
    theme = _theme(session, "memory-demand", 80.0)
    _expose(session, theme, _company(session, "direct", "DIR"), 90.0, order=1)
    _expose(session, theme, _company(session, "distant", "DST"), 30.0, order=3)
    return theme


def test_ratings_are_on_a_zero_to_ten_scale(session: Session, universe: Theme) -> None:
    ratings = rate_companies(session, as_of=AS_OF, persist=False)
    assert ratings
    for rating in ratings:
        assert 0.0 <= rating.rating <= 10.0
        # One decimal, because no component is measured finely enough to justify more.
        assert rating.rating == round(rating.rating, 1)


def test_a_directly_exposed_company_outranks_a_distant_one(
    session: Session, universe: Theme
) -> None:
    ratings = rate_companies(session, as_of=AS_OF, persist=False)
    order = [rating.company_key for rating in ratings]
    assert order.index("direct") < order.index("distant")


def test_a_company_with_no_exposure_is_absent_rather_than_rated_zero(
    session: Session, universe: Theme
) -> None:
    """'No evidence about this company' and 'the evidence says nothing' differ."""
    _company(session, "unconnected", "UNC")
    session.flush()
    keys = {rating.company_key for rating in rate_companies(session, as_of=AS_OF, persist=False)}
    assert "unconnected" not in keys


def test_a_competitor_is_a_headwind_not_a_tailwind(session: Session, universe: Theme) -> None:
    """A rising theme does not help a competitor of its beneficiary.

    Listing both as 'trending' without the distinction is the most misleading thing this
    output could do.
    """
    rival = _company(session, "rival", "RVL")
    _expose(session, universe, rival, 70.0, role=ExposureRole.COMPETITOR)
    ratings = {r.company_key: r for r in rate_companies(session, as_of=AS_OF, persist=False)}
    assert ratings["rival"].direction == "headwind"
    assert ratings["direct"].direction == "tailwind"


def test_price_confirmation_is_unavailable_and_its_weight_redistributed(
    session: Session, universe: Theme
) -> None:
    """No market-data provider is configured, so this must never be estimated."""
    rating = rate_companies(session, as_of=AS_OF, persist=False)[0]
    price = next(c for c in rating.components if c["key"] == "price_confirmation")
    assert price["available"] is False
    assert price["normalized"] is None
    assert price["effective_weight"] == 0.0
    assert "market-data" in price["explanation"]
    # The remaining components must carry the full weight between them.
    assert sum(c["effective_weight"] for c in rating.components) == pytest.approx(1.0)


def test_a_rating_decomposes_into_its_reasons(session: Session, universe: Theme) -> None:
    rating = rate_companies(session, as_of=AS_OF, persist=False)[0]
    keys = {component["key"] for component in rating.components}
    assert {"theme_trend", "theme_acceleration", "exposure", "corroboration"} <= keys
    assert rating.rationale
    for component in rating.components:
        assert component["explanation"]


def test_demo_evidence_never_produces_a_live_rating(session: Session) -> None:
    """Weakest-wins: a rating built on synthetic evidence is DEMO however it is shown."""
    theme = _theme(session, "demo-theme", 70.0)
    theme.data_mode = DataMode.DEMO
    _expose(session, theme, _company(session, "fictional", "FIC"), 80.0, mode=DataMode.DEMO)
    session.flush()
    rated = rate_companies(session, as_of=AS_OF, persist=False)
    rating = next(r for r in rated if r.company_key == "fictional")
    assert rating.data_mode is DataMode.DEMO


def test_breadth_adds_a_bounded_bonus_not_a_multiple(session: Session) -> None:
    """Exposure to five weak themes must not outrank being central to one real move."""
    company = _company(session, "broad", "BRD")
    for index in range(6):
        theme = _theme(session, f"theme-{index}", 40.0)
        _expose(session, theme, company, 40.0)
    session.flush()

    focused_company = _company(session, "focused", "FOC")
    strong = _theme(session, "strong-theme", 95.0)
    _expose(session, strong, focused_company, 95.0)
    session.flush()

    ratings = {r.company_key: r for r in rate_companies(session, as_of=AS_OF, persist=False)}
    assert ratings["focused"].score > ratings["broad"].score
    assert ratings["broad"].theme_count == 6


def test_ratings_are_persisted_with_their_decomposition(
    session: Session, universe: Theme
) -> None:
    rate_companies(session, as_of=AS_OF, persist=True)
    stored = session.scalars(
        select(Score).where(Score.subject_type == "COMPANY")
    ).all()
    assert stored
    assert all(row.model_name == "company_trend_score" for row in stored)
    assert all(0.0 <= row.value <= 100.0 for row in stored)


def test_no_themes_means_no_ratings_rather_than_an_empty_claim(session: Session) -> None:
    _company(session, "alone", "ALN")
    session.flush()
    assert rate_companies(session, as_of=AS_OF, persist=False) == []


def test_the_bonus_cap_is_respected(session: Session) -> None:
    assert MAX_THEME_BONUS <= 0.25, "breadth must stay corroborating, not multiplying"
