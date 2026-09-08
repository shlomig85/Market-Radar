"""Score models: versioning, renormalisation and refusal to invent."""

from __future__ import annotations

import pytest

from marketradar.errors import ValidationError
from marketradar.scoring import (
    CONFIDENCE_V1,
    OPPORTUNITY_V1,
    TREND_V1,
    ComponentSpec,
    ScoreModelSpec,
    compute_score,
    unavailable,
    value,
)
from marketradar.scoring.models import ALL_MODELS


def test_every_model_weights_to_one():
    for model in ALL_MODELS:
        assert sum(c.weight for c in model.components) == pytest.approx(1.0)


def test_a_model_with_bad_weights_cannot_be_defined():
    with pytest.raises(ValueError, match="must sum to 1.0"):
        ScoreModelSpec(
            name="broken",
            version="1.0.0",
            description="",
            components=(ComponentSpec("a", "A", 0.5, ""), ComponentSpec("b", "B", 0.2, "")),
        )


def test_unresolved_components_are_rejected():
    """Silence about a component is the one thing not allowed."""
    with pytest.raises(ValidationError, match="were not resolved"):
        compute_score(TREND_V1, {"signal_strength": value(50, 50, "x")})


def test_unavailable_components_are_dropped_and_weights_renormalised():
    values = {key: value(70, 70, "ok") for key in TREND_V1.component_keys}
    full = compute_score(TREND_V1, values)
    assert full.weight_coverage == pytest.approx(1.0)
    assert full.value == pytest.approx(70.0)

    for key in ("economic_impact", "market_mispricing", "catalyst_proximity"):
        values[key] = unavailable("no provider")
    partial = compute_score(TREND_V1, values)

    assert partial.weight_coverage == pytest.approx(0.75)
    assert sorted(partial.unavailable_components) == [
        "catalyst_proximity",
        "economic_impact",
        "market_mispricing",
    ]
    # Every remaining weight scaled by 1/0.75.
    strength = next(c for c in partial.components if c.key == "signal_strength")
    # Effective weights are rounded to 6 dp for storage, hence the explicit tolerance.
    assert strength.effective_weight == pytest.approx(0.20 / 0.75, abs=1e-6)
    assert sum(c.effective_weight for c in partial.components) == pytest.approx(1.0, abs=1e-5)


def test_an_unavailable_component_is_never_treated_as_zero():
    """The failure this guards against: a missing input silently dragging a score down."""
    high = {key: value(90, 90, "ok") for key in TREND_V1.component_keys}
    with_gap = dict(high)
    with_gap["market_mispricing"] = unavailable("no market data")

    assert compute_score(TREND_V1, with_gap).value == pytest.approx(
        compute_score(TREND_V1, high).value
    )

    as_zero = dict(high)
    as_zero["market_mispricing"] = value(0, 0, "wrongly defaulted")
    assert compute_score(TREND_V1, as_zero).value < compute_score(TREND_V1, with_gap).value


def test_a_score_with_no_available_components_is_not_computable():
    values = {key: unavailable("nothing configured") for key in TREND_V1.component_keys}
    result = compute_score(TREND_V1, values)
    assert not result.is_computable
    assert result.weight_coverage == 0.0


def test_components_carry_their_explanation_and_inputs():
    values = {key: value(60, 60, f"because of {key}") for key in OPPORTUNITY_V1.component_keys}
    result = compute_score(OPPORTUNITY_V1, values)
    assert all(c.explanation for c in result.components)
    assert result.to_dict()["version"] == OPPORTUNITY_V1.version


def test_normalised_values_are_clamped():
    values = {key: value(500, 500, "over") for key in CONFIDENCE_V1.component_keys}
    result = compute_score(CONFIDENCE_V1, values)
    assert result.value == 100.0
    assert all((c.normalized or 0) <= 100.0 for c in result.components)


def test_score_value_is_bounded():
    values = {key: value(-50, -50, "under") for key in CONFIDENCE_V1.component_keys}
    assert compute_score(CONFIDENCE_V1, values).value == 0.0
