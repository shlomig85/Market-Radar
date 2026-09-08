"""Independence scoring: amplification must never read as confirmation."""

from __future__ import annotations

import pytest

from marketradar.domain.enums import SourceClass
from marketradar.evidence.independence import (
    EvidenceDescriptor,
    normalised_entropy,
    profile,
)


def _item(evidence_id, cluster_id, source_class, quality, primary=False):
    return EvidenceDescriptor(evidence_id, cluster_id, source_class, quality, primary)


def test_empty_evidence_is_zero_everywhere():
    result = profile([])
    assert result.independent_source_count == 0
    assert result.source_diversity == 0.0
    assert result.avg_source_quality == 0.0


def test_ten_copies_of_one_story_are_one_confirmation():
    """The property the whole product depends on."""
    evidence = [
        _item(f"e{i}", "cluster-1", SourceClass.FINANCIAL_MEDIA, 85) for i in range(10)
    ]
    result = profile(evidence)
    assert result.evidence_count == 10
    assert result.independent_source_count == 1
    assert result.is_single_sourced
    assert result.amplification_ratio == pytest.approx(0.1)


def test_independent_sources_raise_the_count():
    evidence = [
        _item("e1", "c1", SourceClass.REGULATORY, 100, True),
        _item("e2", "c2", SourceClass.GOVERNMENT, 96, True),
        _item("e3", "c3", SourceClass.INDUSTRY, 88),
    ]
    result = profile(evidence)
    assert result.independent_source_count == 3
    assert result.amplification_ratio == pytest.approx(1.0)
    assert result.primary_source_ratio == pytest.approx(2 / 3)


def test_adding_duplicates_never_increases_independence():
    base = [
        _item("e1", "c1", SourceClass.REGULATORY, 100, True),
        _item("e2", "c2", SourceClass.INDUSTRY, 88),
    ]
    padded = base + [_item(f"dup{i}", "c1", SourceClass.FINANCIAL_MEDIA, 70) for i in range(20)]
    assert profile(padded).independent_source_count == profile(base).independent_source_count
    assert profile(padded).amplification_ratio < profile(base).amplification_ratio


def test_diversity_rewards_spread_across_classes():
    concentrated = [
        _item(f"e{i}", f"c{i}", SourceClass.FINANCIAL_MEDIA, 85) for i in range(3)
    ]
    spread = [
        _item("e1", "c1", SourceClass.REGULATORY, 100, True),
        _item("e2", "c2", SourceClass.GOVERNMENT, 96, True),
        _item("e3", "c3", SourceClass.TECHNICAL, 80),
    ]
    assert profile(spread).source_diversity > profile(concentrated).source_diversity


def test_cluster_is_represented_by_its_highest_authority_member():
    evidence = [
        _item("blog", "c1", SourceClass.COMMUNITY, 35),
        _item("filing", "c1", SourceClass.REGULATORY, 100, True),
    ]
    result = profile(evidence)
    assert result.avg_source_quality == 100
    assert result.primary_source_ratio == 1.0


def test_entropy_is_never_negative():
    """Floating point can produce -0.0; downstream scores must never see it."""
    assert normalised_entropy([5, 0, 0, 0]) == 0.0
    assert normalised_entropy([]) == 0.0
    assert 0.0 <= normalised_entropy([2, 2, 2]) <= 1.0
