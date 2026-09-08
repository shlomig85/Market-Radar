"""Information ancestry: repeated reporting must collapse to one confirmation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from marketradar.domain.enums import ClusterMethod
from marketradar.evidence.clustering import ClusterCandidate, assign_clusters
from marketradar.ingestion.hashing import content_hash

BASE = datetime(2026, 8, 1, tzinfo=UTC)
ANNOUNCEMENT = (
    "Northbridge Memory Corp announced a multi-year supply agreement covering "
    "high-bandwidth memory. The company said demand is accelerating and capacity for the "
    "coming year is substantially committed."
)


def _candidate(doc_id, text, days=0, quality=80, origin_ref=None):
    return ClusterCandidate(
        document_id=doc_id,
        content_hash=content_hash(text),
        text=text,
        event_at=BASE,
        published_at=BASE + timedelta(days=days),
        source_quality=quality,
        origin_ref=origin_ref,
    )


def test_declared_origin_groups_documents():
    ref = "demo:announcement:1"
    candidates = [
        _candidate("press-release", ANNOUNCEMENT, 0, 95, ref),
        _candidate("newspaper", "A different retelling entirely.", 1, 85, ref),
        _candidate("blog", "Yet another phrasing with no overlap at all.", 2, 40, ref),
    ]
    clusters = assign_clusters(candidates)
    assert len(clusters) == 1
    assert clusters[0].size == 3
    assert clusters[0].members["newspaper"] == ClusterMethod.DECLARED_ORIGIN


def test_verbatim_syndication_is_one_cluster():
    candidates = [
        _candidate("wire", ANNOUNCEMENT, 0, 88),
        _candidate("syndicated", ANNOUNCEMENT, 1, 70),
    ]
    clusters = assign_clusters(candidates)
    assert len(clusters) == 1
    assert clusters[0].members["syndicated"] == ClusterMethod.EXACT_HASH


def test_near_duplicate_rewrite_is_one_cluster():
    """A genuinely reworded copy — not merely re-punctuated — still collapses."""
    rewrite = (
        "Northbridge Memory Corp has announced a multi-year supply agreement covering "
        "high-bandwidth memory. The company said demand is accelerating and capacity for the "
        "coming year is substantially committed, according to the statement."
    )
    clusters = assign_clusters([_candidate("a", ANNOUNCEMENT), _candidate("b", rewrite, 1)])
    assert len(clusters) == 1
    assert clusters[0].members["b"] == ClusterMethod.NEAR_DUPLICATE


def test_punctuation_only_edits_are_caught_as_exact_duplicates():
    """Normalisation means re-punctuated syndication is exact, not merely near, duplication."""
    repunctuated = ANNOUNCEMENT.replace("multi-year", "multi year").replace("Corp", "Corp.")
    clusters = assign_clusters(
        [_candidate("a", ANNOUNCEMENT), _candidate("b", repunctuated, 1)]
    )
    assert len(clusters) == 1
    assert clusters[0].members["b"] == ClusterMethod.EXACT_HASH


def test_independent_documents_are_not_merged():
    clusters = assign_clusters(
        [
            _candidate("a", "Memory contract pricing rose in the latest negotiation round."),
            _candidate("b", "Government export statistics showed higher declared unit values."),
            _candidate("c", "An equipment supplier reported materially higher order intake."),
        ]
    )
    assert len(clusters) == 3


def test_origin_is_earliest_then_highest_authority():
    ref = "demo:announcement:2"
    early_low = ClusterCandidate(
        "blog", content_hash("x"), "x", BASE + timedelta(days=1), BASE, 30, ref
    )
    early_high = ClusterCandidate(
        "filing", content_hash("y"), "y", BASE, BASE, 100, ref
    )
    late_high = ClusterCandidate(
        "wire", content_hash("z"), "z", BASE + timedelta(days=2), BASE, 95, ref
    )
    clusters = assign_clusters([early_low, early_high, late_high])
    assert clusters[0].origin_document_id == "filing"
    assert clusters[0].members["filing"] == ClusterMethod.ORIGIN


def test_clustering_is_deterministic_regardless_of_input_order():
    ref = "demo:announcement:3"
    items = [
        _candidate("a", ANNOUNCEMENT, 0, 95, ref),
        _candidate("b", ANNOUNCEMENT, 1, 70, ref),
        _candidate("c", "Something unrelated about shipping rates.", 2, 60),
    ]
    forward = assign_clusters(items)
    backward = assign_clusters(list(reversed(items)))
    assert {c.origin_document_id for c in forward} == {c.origin_document_id for c in backward}
    assert sorted(c.size for c in forward) == sorted(c.size for c in backward)
