"""Subject discovery (audit C6).

The finding: the system could only recognise three subjects, because a human had typed
their vocabulary into a lexicon. A system that can only find themes it was taught monitors;
it does not discover.

The controlling test here is the first one — a topic that appears nowhere in this codebase's
vocabulary must still be found. Everything else guards the ways a discovery engine invents
topics that are not really there.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from marketradar.evidence.extractor import SUBJECT_LEXICON
from marketradar.subjects.discovery import (
    MIN_CLUSTERS,
    STOPWORDS,
    SubjectObservation,
    candidate_terms,
    discover_subjects,
    normalise_term,
    subject_key_for,
)

AS_OF = datetime(2026, 9, 9, tzinfo=UTC)


def obs(index: int, text: str, cluster: str | None, days_ago: int) -> SubjectObservation:
    return SubjectObservation(f"d{index}", text, AS_OF - timedelta(days=days_ago), cluster)


def terms(candidates) -> set[str]:
    return {candidate.term for candidate in candidates}


#: A corpus about grid storage. The phrase appears nowhere in SUBJECT_LEXICON, in STOPWORDS,
#: or anywhere else in this repository — which is the whole point.
GRID_STORAGE = [
    obs(1, "Utilities are accelerating grid storage deployment as battery costs fall.", "c1", 5),
    obs(2, "Grid storage procurement rose sharply across three states.", "c2", 8),
    obs(3, "Demand for grid storage is outpacing installed manufacturing capacity.", "c3", 12),
    obs(4, "Battery cell suppliers report grid storage orders extending lead times.", "c4", 15),
    obs(5, "Analysts note grid storage interconnection queues continue to lengthen.", "c5", 20),
]


# ------------------------------------------------------------ the point
def test_a_subject_nobody_taught_the_system_is_discovered() -> None:
    found = discover_subjects(GRID_STORAGE, as_of=AS_OF)
    assert "grid storage" in terms(found)


def test_the_discovered_subject_is_genuinely_untaught() -> None:
    """Guards the test above from becoming vacuous if the lexicon ever grows."""
    taught = {word for vocabulary in SUBJECT_LEXICON.values() for word in vocabulary}
    assert "grid storage" not in taught
    assert not any("grid" in word or "storage" in word for word in taught)
    assert "grid" not in STOPWORDS and "storage" not in STOPWORDS


def test_a_subject_gets_a_stable_key() -> None:
    assert subject_key_for("high bandwidth memory") == "high_bandwidth_memory"
    assert subject_key_for("Grid Storage") == subject_key_for("grid storage")


# ------------------------------------------------- not inventing topics
def test_an_echo_chamber_cannot_mint_a_subject() -> None:
    """Five documents repeating one announcement are ONE confirmation, not five.

    This is the ancestry clustering doing the load-bearing work: without counting
    independent origins, any syndicated story becomes a discovered 'theme'.
    """
    echo = [
        obs(index, "Quantum annealing breakthrough claimed by a startup.", "one-cluster", 6)
        for index in range(10, 20)
    ]
    assert not any("quantum" in term for term in terms(discover_subjects(echo, as_of=AS_OF)))


def test_support_below_the_independence_floor_is_rejected() -> None:
    two_origins = [
        obs(1, "Sodium ion chemistry is gaining share in stationary applications.", "a", 5),
        obs(2, "Sodium ion chemistry shipments rose this period.", "b", 6),
    ]
    assert not any("sodium" in term for term in terms(discover_subjects(two_origins, as_of=AS_OF)))
    # ... and is accepted once a third independent origin reports it.
    two_origins.append(obs(3, "Sodium ion chemistry adoption widened again.", "c", 7))
    assert any("sodium ion" in term for term in terms(discover_subjects(two_origins, as_of=AS_OF)))


def test_boilerplate_never_becomes_a_subject() -> None:
    """'The company reported results for the quarter' is in every filing ever written."""
    boilerplate = [
        obs(
            index,
            "The company reported results for the quarter. Management said revenue "
            "increased. This release contains forward looking statements.",
            f"c{index}",
            index,
        )
        for index in range(1, 9)
    ]
    found = terms(discover_subjects(boilerplate, as_of=AS_OF))
    for phrase in ("company", "quarter", "revenue", "forward looking statement", "management"):
        assert phrase not in found, phrase


def test_a_term_is_not_reported_three_times_as_its_own_fragments() -> None:
    """'grid', 'storage' and 'grid storage' are one topic, not three."""
    found = terms(discover_subjects(GRID_STORAGE, as_of=AS_OF))
    assert "grid storage" in found
    assert "grid" not in found
    assert "storage" not in found


# ------------------------------------------------------------- temporal
def test_a_document_published_after_the_as_of_instant_is_invisible() -> None:
    """A replay must discover what was discoverable then, not what is obvious now."""
    future = [*GRID_STORAGE, obs(6, "Fusion ignition milestone reached.", "f1", -10)]
    assert not any("fusion" in term for term in terms(discover_subjects(future, as_of=AS_OF)))


def test_discovery_is_empty_rather_than_guessing_on_an_empty_corpus() -> None:
    assert discover_subjects([], as_of=AS_OF) == []


def test_an_emerging_subject_outranks_a_long_standing_one() -> None:
    """The product's question is 'what is CHANGING', not 'what is discussed most'."""
    corpus = []
    # A steady background topic, evenly spread across the baseline and the window.
    for index in range(1, 13):
        corpus.append(
            obs(
                index,
                "Copper wiring remains standard in distribution networks.",
                f"bg{index}",
                index * 10,
            )
        )
    # A newly accelerating one, confined to the recent window.
    for index in range(20, 26):
        corpus.append(
            obs(
                index,
                "Solid state batteries are entering pilot production lines.",
                f"new{index}",
                3,
            )
        )
    ranked = discover_subjects(corpus, as_of=AS_OF)
    positions = {candidate.term: rank for rank, candidate in enumerate(ranked)}
    emerging = next((p for t, p in positions.items() if "solid state" in t), None)
    background = next((p for t, p in positions.items() if "copper" in t), None)
    assert emerging is not None, ranked
    if background is not None:
        assert emerging < background, "an accelerating subject must rank above a steady one"


def test_unclustered_documents_count_as_separate_origins() -> None:
    """Lumping every unclustered document together would understate independence to one."""
    loose = [
        obs(index, "Rare earth separation capacity is expanding.", None, index)
        for index in range(1, 6)
    ]
    assert any("rare earth" in term for term in terms(discover_subjects(loose, as_of=AS_OF)))


# --------------------------------------------------------------- detail
def test_candidate_terms_never_start_or_end_with_a_stopword() -> None:
    found = candidate_terms("The demand for memory is increasing across the market.")
    for term in found:
        first, last = term.split()[0], term.split()[-1]
        assert first not in STOPWORDS, term
        assert last not in STOPWORDS, term


def test_terms_do_not_span_a_sentence_boundary() -> None:
    """A phrase that straddles a full stop is not a phrase."""
    found = candidate_terms("Utilities bought grid storage. Solar inverters shipped widely.")
    assert "grid storage" in found
    assert "storage solar" not in found, "a term must not cross a sentence boundary"


def test_change_words_are_never_subjects_on_their_own() -> None:
    """The first live run returned 'capacity', 'pricing', 'demand', 'inventory'.

    Every one is a predicate — what is HAPPENING, already modelled as an EventType — rather
    than a subject, which is the thing it is happening to. The exclusion is derived from the
    extraction rules themselves, so adding a rule keeps it current with nobody maintaining
    a list.
    """
    found = candidate_terms(
        "Capacity expanded and pricing rose while inventory declined and demand accelerated."
    )
    for predicate in ("capacity", "pricing", "inventory", "demand", "declined", "expanded"):
        assert predicate not in found, predicate


def test_a_term_may_not_end_on_a_change_word() -> None:
    """'memory pricing' is not a subject: it is the subject 'memory' under the pricing
    signal template, which the signal layer generates for every subject anyway."""
    found = candidate_terms("Grid storage pricing rose sharply this period.")
    assert "grid storage pricing" not in found
    assert "grid storage" in found


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("memories", "memory"), ("batteries", "battery"), ("chips", "chip"), ("glass", "glass")],
)
def test_plurals_fold_but_nothing_else_does(raw: str, expected: str) -> None:
    """Over-stemming invents subjects by merging unrelated words; it is worse than under."""
    assert normalise_term(raw) == expected


def test_capacity_and_capacitor_are_never_merged() -> None:
    assert normalise_term("capacity") != normalise_term("capacitor")


def test_every_candidate_reports_what_earned_it_its_place() -> None:
    candidate = next(
        c for c in discover_subjects(GRID_STORAGE, as_of=AS_OF) if c.term == "grid storage"
    )
    assert candidate.cluster_count >= MIN_CLUSTERS
    assert candidate.document_count == 5
    assert 0.0 <= candidate.specificity <= 1.0
    assert candidate.example_documents, "a discovered subject must be traceable to documents"
    assert candidate.first_seen_at <= candidate.last_seen_at


# ------------------------------------------- observed on real feed data
def test_a_publishers_own_furniture_never_becomes_a_subject() -> None:
    """A live run over real feeds returned "may earn compensation", "affiliate link
    policy" and "california privacy right" as discovered subjects.

    Those are cookie notices, affiliate disclosures and copyright footers: on every page a
    site publishes and on nobody else's. The corpus-wide ratio cannot catch them — one
    publisher's footer is a small share of a multi-publisher corpus — so saturation is
    measured per source, which needs no list of known boilerplate phrases.
    """
    corpus = []
    footer = "Ars Technica may earn compensation on sales from affiliate link policy pages."
    for index in range(8):
        corpus.append(
            SubjectObservation(
                document_id=f"ars{index}",
                text=f"Report {index} on solid state batteries entering production. {footer}",
                published_at=AS_OF - timedelta(days=index + 1),
                cluster_id=f"ars-c{index}",
                source_key="arstechnica",
            )
        )
    # A second publisher covering the same topic WITHOUT that footer. Furniture is the
    # contrast between sources, so there has to be something to contrast against.
    for index in range(6):
        corpus.append(
            SubjectObservation(
                document_id=f"journal{index}",
                text="Solid state batteries are moving into pilot production lines.",
                published_at=AS_OF - timedelta(days=index + 1),
                cluster_id=f"journal-c{index}",
                source_key="journal",
            )
        )
    found = terms(discover_subjects(corpus, as_of=AS_OF))
    for furniture in ("earn compensation", "affiliate link policy", "affiliate link"):
        assert furniture not in found, furniture
    # The actual topic survives the filter that removes the furniture around it.
    assert any("solid state" in term for term in found)


def test_furniture_detection_needs_enough_documents_from_that_source() -> None:
    """Two documents from one publisher say nothing about what that publisher repeats."""
    corpus = [
        SubjectObservation(
            document_id=f"d{index}",
            text="Perovskite tandem cells reached a new efficiency milestone.",
            published_at=AS_OF - timedelta(days=index + 1),
            cluster_id=f"c{index}",
            source_key="journal",
        )
        for index in range(3)
    ]
    assert any("perovskite" in term for term in terms(discover_subjects(corpus, as_of=AS_OF)))
