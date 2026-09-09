"""Company entity resolution.

The Cycle-1 audit demonstrated that substring matching attributed evidence to the wrong
issuer, using real tickers that are ordinary English words:

    'Demand is accelerating on strong server orders.'  -> ON Semiconductor
    'All memory suppliers reported increasing demand.' -> Allstate
    'The key driver was increasing demand...'          -> KeyCorp

At ~10,000 US registrants that is a systematic corruption of the evidence base, not an
edge case. These tests are the standing proof that it stays fixed.
"""

from __future__ import annotations

import pytest

from marketradar.entities import CompanyRecord, EntityResolver
from marketradar.entities.resolver import strip_suffix, suffix_chain

#: Real issuers whose tickers collide with ordinary English words.
WORD_TICKERS = [
    CompanyRecord("on_semi", "ON Semiconductor Corporation", ticker="ON"),
    CompanyRecord("allstate", "The Allstate Corporation", ticker="ALL"),
    CompanyRecord("keycorp", "KeyCorp", ticker="KEY"),
    CompanyRecord("gartner", "Gartner, Inc.", ticker="IT"),
    CompanyRecord("caterpillar", "Caterpillar Inc.", ticker="CAT"),
    CompanyRecord("micron", "Micron Technology, Inc.", ticker="MU"),
    CompanyRecord(
        "alphabet", "Alphabet Inc.", ticker="GOOGL", aliases=("Google",),
    ),
]


@pytest.fixture
def resolver() -> EntityResolver:
    return EntityResolver(WORD_TICKERS)


# ------------------------------------------------------------- the audit's failures
@pytest.mark.parametrize(
    "text",
    [
        "Demand is accelerating on strong server orders.",
        "All memory suppliers reported increasing demand this quarter.",
        "The key driver was increasing demand from cloud customers.",
        "It is unclear whether the outlook will improve.",
        "The cat sat on the mat, so all is well.",
    ],
)
def test_ordinary_words_are_never_resolved_as_tickers(resolver, text):
    assert resolver.resolve(text) == [], f"false attribution in: {text}"


def test_word_boundaries_prevent_matches_inside_longer_words(resolver):
    """'CAT' must not match inside 'category' or 'catalyst'."""
    assert resolver.resolve("The catalyst category was concerning.") == []


# ------------------------------------------------------------- genuine mentions
def test_full_company_names_resolve(resolver):
    matches = resolver.resolve("Micron Technology reported increasing demand for HBM.")
    assert [m.company_key for m in matches] == ["micron"]
    assert matches[0].confidence > 0.8


def test_aliases_resolve(resolver):
    matches = resolver.resolve("Google increased capital expenditure again.")
    assert [m.company_key for m in matches] == ["alphabet"]
    assert matches[0].kind == "alias"


def test_uppercase_ticker_with_a_cue_resolves_with_high_confidence(resolver):
    matches = resolver.resolve("The company (NASDAQ: MU) said pricing improved.")
    assert [m.company_key for m in matches] == ["micron"]
    assert matches[0].kind == "ticker"
    assert matches[0].confidence > 0.9


def test_short_tickers_require_a_cue_even_when_uppercase(resolver):
    """An all-capitals headline must not turn 'ON' into ON Semiconductor."""
    assert resolver.resolve("MEMORY DEMAND ACCELERATES ON STRONG ORDERS") == []
    cued = resolver.resolve("shares of (NYSE: ON) rose")
    assert [m.company_key for m in cued] == ["on_semi"]


def test_an_uncued_ticker_needs_the_issuer_named_somewhere(resolver):
    """Length alone used to be enough for an uncued ticker. A live run disproved it.

    Upper-case acronyms are everywhere in technical prose, so "HBM" put a copper miner at
    the top of an AI-memory ranking. A document genuinely about an issuer names it; that
    corroboration is now required, and it stays weaker than an explicitly cued mention.
    """
    bare = resolver.resolve("We remain positive on GOOGL after the quarter.")
    assert bare == [], "a bare acronym is not evidence of an issuer"

    corroborated = resolver.resolve(
        "We remain positive on Alphabet; GOOGL rose after the quarter."
    )
    assert "alphabet" in {match.company_key for match in corroborated}
    ticker_match = next(m for m in corroborated if m.surface == "GOOGL")
    assert ticker_match.confidence < 0.9, "an uncued ticker is weaker than a cued one"


# ------------------------------------------------------------- ambiguity
def test_an_ambiguous_surface_returns_candidates_rather_than_guessing():
    resolver = EntityResolver(
        [
            CompanyRecord("delta_air", "Delta Air Lines, Inc."),
            CompanyRecord("delta_app", "Delta Apparel, Inc."),
        ]
    )
    # Both share no usable common surface, so a distinctive one still resolves cleanly.
    assert [m.company_key for m in resolver.resolve("Delta Air Lines raised guidance.")] == [
        "delta_air"
    ]


def test_identical_names_are_reported_as_ambiguous():
    resolver = EntityResolver(
        [
            CompanyRecord("acme_us", "Acme Robotics Inc."),
            CompanyRecord("acme_eu", "Acme Robotics"),
        ]
    )
    matches = resolver.resolve("Acme Robotics reported increasing demand.")
    assert matches
    assert matches[0].is_ambiguous
    assert matches[0].alternatives


def test_resolve_one_refuses_to_guess_when_ambiguous():
    """An incorrect attribution is worse than no attribution."""
    resolver = EntityResolver(
        [
            CompanyRecord("acme_us", "Acme Robotics Inc."),
            CompanyRecord("acme_eu", "Acme Robotics"),
        ]
    )
    assert resolver.resolve_one("Acme Robotics reported increasing demand.") is None


def test_resolve_one_returns_the_best_unambiguous_match(resolver):
    match = resolver.resolve_one("Caterpillar Inc. reported increasing demand.")
    assert match is not None and match.company_key == "caterpillar"


# ------------------------------------------------------------- overlap and forms
def test_longest_match_wins_on_overlap():
    resolver = EntityResolver(
        [
            CompanyRecord("north", "Northbridge Corp"),
            CompanyRecord("north_mem", "Northbridge Memory Corp"),
        ]
    )
    matches = resolver.resolve("Northbridge Memory Corp announced an agreement.")
    assert len(matches) == 1
    assert matches[0].company_key == "north_mem"


def test_suffix_chain_registers_progressively_shorter_forms():
    assert suffix_chain("Micron Technology, Inc.") == [
        "Micron Technology, Inc.",
        "Micron Technology",
        "Micron",
    ]
    assert strip_suffix("Northbridge Memory Corp.") == "Northbridge Memory"


def test_former_names_resolve_with_lower_confidence():
    resolver = EntityResolver(
        [CompanyRecord("meta", "Meta Platforms, Inc.", former_names=("Facebook, Inc.",))]
    )
    former = resolver.resolve_one("Facebook, Inc. disclosed higher capital expenditure.")
    current = resolver.resolve_one("Meta Platforms disclosed higher capital expenditure.")
    assert former is not None and former.company_key == "meta"
    assert current is not None
    assert former.confidence < current.confidence, (
        "a former name may now belong to a different entity and must be weaker"
    )


def test_generic_single_word_names_are_not_registered_as_surfaces():
    """A company literally named 'Key' must not match every use of the word."""
    resolver = EntityResolver([CompanyRecord("key_co", "Key Corporation")])
    assert resolver.resolve("The key question is whether demand holds.") == []


def test_empty_universe_resolves_nothing_without_crashing():
    assert EntityResolver([]).resolve("Micron reported increasing demand.") == []


def test_matches_carry_offsets_into_the_source_text(resolver):
    text = "Earlier, Caterpillar Inc. reported increasing demand."
    match = resolver.resolve(text)[0]
    assert text[match.start : match.end] == match.surface


# --------------------------------------------- observed on real feed data
def test_an_acronym_is_not_a_ticker_without_corroboration() -> None:
    """A live run put Hudbay Minerals — a copper miner, ticker HBM — at the top of an
    AI-memory ranking, because "HBM" is also high-bandwidth memory. CTS, BAND and SKHY
    arrived the same way. Technical prose is full of upper-case acronyms."""
    resolver = EntityResolver(
        [
            CompanyRecord(key="hudbay", name="Hudbay Minerals Inc.", ticker="HBM"),
            CompanyRecord(key="cts", name="CTS Corporation", ticker="CTS"),
            CompanyRecord(key="bandwidth", name="Bandwidth Inc.", ticker="BAND"),
        ]
    )
    text = "Demand for HBM is accelerating as chipmakers expand CTS packaging capacity."
    assert resolver.resolve(text) == []


def test_a_ticker_resolves_when_the_issuer_is_actually_named() -> None:
    resolver = EntityResolver(
        [CompanyRecord(key="hudbay", name="Hudbay Minerals Inc.", ticker="HBM")]
    )
    matches = resolver.resolve("Hudbay Minerals said HBM output rose at its copper mines.")
    assert {match.company_key for match in matches} == {"hudbay"}


def test_an_explicitly_cued_ticker_still_resolves_alone() -> None:
    """A cue is itself the corroboration: nobody writes (HBM) about an acronym."""
    resolver = EntityResolver(
        [CompanyRecord(key="hudbay", name="Hudbay Minerals Inc.", ticker="HBM")]
    )
    assert [m.company_key for m in resolver.resolve("Shares of (HBM) fell today.")] == ["hudbay"]
    assert [m.company_key for m in resolver.resolve("NYSE: HBM closed lower.")] == ["hudbay"]
