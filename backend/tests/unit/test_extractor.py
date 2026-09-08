"""Rule-based evidence extraction."""

from __future__ import annotations

from marketradar.domain.enums import EventType
from marketradar.evidence.extractor import (
    extract,
    is_forward_looking,
    is_stability_statement,
    split_sentences,
)


def test_span_offsets_point_at_the_real_text():
    text = "First sentence here. Contract pricing increased again this quarter."
    results = extract(text)
    assert results
    item = results[0]
    assert text[item.excerpt_start : item.excerpt_end].strip() == item.excerpt


def test_stability_statements_produce_no_evidence():
    """'Nothing changed' must never become a change event."""
    for sentence in (
        "Contract pricing was little changed versus the prior quarter.",
        "Inventory levels remained within the normal historical range.",
        "Results were in line with expectations.",
        "Order intake was described as steady.",
    ):
        assert is_stability_statement(sentence)
        assert extract(sentence) == []


def test_forward_looking_statements_are_evidence_but_not_events():
    text = (
        "We caution that announced capacity expansion is large relative to plausible "
        "demand growth."
    )
    assert is_forward_looking(text)
    results = extract(text)
    assert results, "a risk argument is still evidence"
    assert all(item.event_type is None for item in results)
    assert all(item.is_forward_looking for item in results)
    assert all(item.magnitude == 0.0 for item in results)


def test_historical_analogy_is_not_an_observation():
    text = "Historically, capacity additions of this scale have been followed by pricing decline."
    results = extract(text)
    assert results
    assert all(item.event_type is None for item in results)


def test_one_sentence_can_carry_several_distinct_claims():
    text = (
        "The company said demand for high-bandwidth memory is accelerating and that its "
        "available capacity for the coming year is now substantially committed."
    )
    types = {item.event_type for item in extract(text)}
    assert EventType.DEMAND_ACCELERATION in types
    assert EventType.SUPPLY_CONSTRAINT in types


def test_a_sentence_yields_at_most_one_claim_per_event_type():
    text = "Demand is accelerating and increasing demand is reported by growing demand indicators."
    demand = [i for i in extract(text) if i.event_type == EventType.DEMAND_ACCELERATION]
    assert len(demand) == 1


def test_direction_is_distinguished():
    up = extract("Contract pricing increased in the latest round.")
    down = extract("Pricing in consumer segments declined during the quarter.")
    assert up[0].event_type == EventType.PRICING_INCREASE
    assert down[0].event_type == EventType.PRICING_DECREASE


def test_subject_detection_uses_document_hints_when_the_sentence_is_generic():
    generic = "Order backlog increased compared with the prior quarter."
    assert extract(generic)[0].subject_key is None
    with_hint = extract(generic, subject_hints=("memory",))
    assert with_hint[0].subject_key == "memory"


def test_entity_resolution_attaches_a_company():
    """The extractor takes a resolution function, not a substring lexicon."""
    results = extract(
        "Northbridge Memory Corp said demand is accelerating.",
        resolve_entity=lambda sentence: "nbmx" if "Northbridge" in sentence else None,
    )
    assert results[0].entity_hint == "nbmx"


def test_no_company_is_attached_when_resolution_declines():
    results = extract(
        "Demand is accelerating on strong orders.", resolve_entity=lambda _s: None
    )
    assert results
    assert all(item.entity_hint is None for item in results)


def test_sentence_splitting_preserves_offsets():
    text = "One. Two! Three?"
    spans = split_sentences(text)
    assert [text[s:e] for s, e, _ in spans] == ["One.", "Two!", "Three?"]
