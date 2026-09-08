"""Signal strength and confidence."""

from __future__ import annotations

import pytest

from marketradar.domain.enums import EventType, SourceClass
from marketradar.evidence.independence import EvidenceDescriptor, profile
from marketradar.signals.definitions import SIGNAL_DEFINITIONS, SIGNALS_BY_KEY
from marketradar.signals.engine import confidence_from_independence, saturating_strength


def test_strength_is_bounded_and_monotonic():
    values = [saturating_strength(x) for x in (0, 0.5, 1, 2, 5, 20, 1000)]
    assert values[0] == 0.0
    assert all(0.0 <= v <= 100.0 for v in values)
    assert values == sorted(values)
    assert values[-1] <= 100.0


def test_strength_ignores_sign():
    assert saturating_strength(3.0) == saturating_strength(-3.0)


def test_confidence_is_capped_for_a_single_source():
    """However authoritative, one source is one source."""
    single = profile(
        [EvidenceDescriptor("e1", "c1", SourceClass.REGULATORY, 100, True)]
    )
    assert confidence_from_independence(single) < 35.0


def test_confidence_rises_with_independent_corroboration():
    def build(n):
        return profile(
            [
                EvidenceDescriptor(f"e{i}", f"c{i}", SourceClass.REGULATORY, 100, True)
                for i in range(n)
            ]
        )

    scores = [confidence_from_independence(build(n)) for n in (1, 2, 4, 8)]
    assert scores == sorted(scores)
    assert scores[-1] > scores[0] * 1.5


def test_confidence_of_no_evidence_is_zero():
    assert confidence_from_independence(profile([])) == 0.0


def test_signal_definitions_are_well_formed():
    keys = [d.key for d in SIGNAL_DEFINITIONS]
    assert len(keys) == len(set(keys)), "signal keys must be unique"
    for definition in SIGNAL_DEFINITIONS:
        assert definition.event_weights, f"{definition.key} has no event weights"
        for event_type, weight in definition.event_weights.items():
            assert isinstance(event_type, EventType)
            assert -1.0 <= weight <= 1.0


def test_capacity_expansion_loosens_supply_tightness():
    """The same event means opposite things to different signals — that is the design."""
    tightness = SIGNALS_BY_KEY["memory_supply_tightness"]
    investment = SIGNALS_BY_KEY["memory_capital_investment"]
    assert tightness.event_weights[EventType.CAPACITY_EXPANSION] < 0
    assert investment.event_weights[EventType.CAPACITY_EXPANSION] > 0


def test_strength_saturates_so_volume_alone_cannot_dominate():
    doubling_contribution = saturating_strength(10.0) - saturating_strength(5.0)
    early_gain = saturating_strength(5.0) - saturating_strength(0.0)
    assert doubling_contribution < early_gain
    assert pytest.approx(saturating_strength(2.5), abs=1.0) == 63.2
