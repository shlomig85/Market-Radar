"""Change detection: the engine must detect change, not popularity."""

from __future__ import annotations

from marketradar.domain.enums import MarketAwareness, TrendMaturity
from marketradar.trends.engine import BASELINE_FLOOR, bounded_change
from marketradar.trends.maturity import MaturityInputs, classify


def test_a_large_but_stable_phenomenon_shows_almost_no_acceleration():
    """The core distinction: 'lots of coverage' is not 'something is changing'."""
    assert abs(bounded_change(85.0, 84.0)) < 5.0


def test_doubling_against_the_baseline_reads_as_strong_acceleration():
    assert bounded_change(60.0, 30.0) > 60.0


def test_decline_is_negative():
    assert bounded_change(20.0, 60.0) < -40.0


def test_change_is_bounded_to_the_scale():
    assert -100.0 <= bounded_change(1000.0, 0.001) <= 100.0
    assert -100.0 <= bounded_change(0.0, 1000.0) <= 100.0


def test_baseline_floor_prevents_a_quiet_history_from_exploding():
    """Without a floor, a near-zero baseline makes any observation look infinite."""
    tiny_baseline = bounded_change(BASELINE_FLOOR / 2, 0.0)
    assert tiny_baseline < 60.0, "a small absolute move off zero is not maximal acceleration"


def test_acceleration_is_monotonic_in_the_observation():
    baseline = 20.0
    values = [bounded_change(x, baseline) for x in (5, 10, 20, 40, 80)]
    assert values == sorted(values)


# ---------------------------------------------------------------- maturity
def _inputs(**overrides):
    base = dict(
        independent_source_count=6,
        acceleration=45.0,
        observation_strength=60.0,
        mainstream_coverage_share=0.1,
        market_reaction_observed=False,
        contradiction_ratio=0.1,
    )
    base.update(overrides)
    return MaturityInputs(**base)


def test_single_source_is_invisible():
    assert classify(_inputs(independent_source_count=1)).maturity == TrendMaturity.INVISIBLE


def test_broad_corroboration_plus_acceleration_is_accelerating():
    assert classify(_inputs()).maturity == TrendMaturity.ACCELERATING


def test_heavy_mainstream_coverage_is_consensus():
    result = classify(_inputs(mainstream_coverage_share=0.5))
    assert result.maturity == TrendMaturity.CONSENSUS
    assert result.market_awareness == MarketAwareness.CONSENSUS


def test_mainstream_coverage_plus_price_reaction_is_crowded():
    result = classify(_inputs(mainstream_coverage_share=0.7, market_reaction_observed=True))
    assert result.maturity == TrendMaturity.CROWDED
    assert result.market_awareness == MarketAwareness.CROWDED


def test_overwhelming_contradiction_invalidates():
    result = classify(_inputs(contradiction_ratio=0.75))
    assert result.maturity == TrendMaturity.INVALIDATED


def test_contradiction_does_not_invalidate_on_thin_evidence():
    """A 100% contradiction rate across two sources is noise, not a refutation."""
    result = classify(_inputs(independent_source_count=2, contradiction_ratio=1.0))
    assert result.maturity != TrendMaturity.INVALIDATED


def test_stalled_evidence_is_mature_not_emerging():
    result = classify(_inputs(independent_source_count=3, acceleration=1.0))
    assert result.maturity == TrendMaturity.MATURE


def test_every_classification_explains_itself():
    for kwargs in ({}, {"independent_source_count": 1}, {"contradiction_ratio": 0.9},
                   {"mainstream_coverage_share": 0.5}, {"acceleration": 1.0,
                                                        "independent_source_count": 3}):
        result = classify(_inputs(**kwargs))
        assert len(result.rationale) > 40
        assert result.classifier_version


def test_awareness_is_unknown_without_mainstream_coverage():
    assert classify(_inputs(mainstream_coverage_share=0.0)).market_awareness == (
        MarketAwareness.UNKNOWN
    )
