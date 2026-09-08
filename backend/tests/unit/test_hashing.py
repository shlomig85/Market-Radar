"""Content normalisation, hashing and shingling."""

from __future__ import annotations

from marketradar.ingestion.hashing import content_hash, jaccard, normalise_text, shingles


def test_normalisation_ignores_punctuation_case_and_spacing():
    a = "Memory pricing INCREASED, again!"
    b = "memory  pricing increased again"
    assert normalise_text(a) == normalise_text(b)
    assert content_hash(a) == content_hash(b)


def test_hash_differs_for_different_content():
    assert content_hash("demand is accelerating") != content_hash("demand is weakening")


def test_hash_is_stable_across_calls():
    text = "Contract pricing for DRAM increased again in the latest negotiation round."
    assert content_hash(text) == content_hash(text)


def test_shingles_of_short_text_do_not_crash():
    assert shingles("short") == frozenset({"short"})
    assert shingles("") == frozenset()


def test_jaccard_bounds():
    left = shingles("the memory market is tightening as demand grows quickly this quarter")
    assert jaccard(left, left) == 1.0
    assert jaccard(left, frozenset()) == 0.0
    other = shingles("unrelated commentary about agricultural commodity export volumes now")
    assert jaccard(left, other) < 0.1


def test_light_editing_keeps_documents_similar():
    original = (
        "Northbridge Memory Corp today announced a multi-year supply agreement covering "
        "high-bandwidth memory for AI accelerator platforms."
    )
    rewritten = (
        "Northbridge Memory Corp today announced a multi year supply agreement covering "
        "high bandwidth memory for AI accelerator platforms!"
    )
    assert jaccard(shingles(original), shingles(rewritten)) > 0.9
