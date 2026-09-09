"""Value-chain relationship extraction (audit C5).

The audit's finding was that every graph edge had been hand-entered and none carried
evidence. These tests hold the replacement to the standard that finding implies: an edge is
produced only when a document *says so*, in a sentence the edge can be traced back to.

The final test measures precision over a labelled set rather than asserting it, because
"the extractor is accurate" is a number, not an opinion.
"""

from __future__ import annotations

import pytest

from marketradar.domain.enums import EntityType, RelationshipType
from marketradar.entities import CompanyRecord, EntityResolver
from marketradar.entities.relationships import (
    RULES,
    extract_relationships,
)

FILER = "sec-0000320193"


@pytest.fixture
def resolver() -> EntityResolver:
    return EntityResolver(
        [
            CompanyRecord(key=FILER, name="Apple Inc.", ticker="AAPL"),
            CompanyRecord(key="msft", name="Microsoft Corporation", ticker="MSFT"),
            CompanyRecord(key="samsung", name="Samsung Electronics"),
            CompanyRecord(
                key="tsmc",
                name="Taiwan Semiconductor Manufacturing Company",
                ticker="TSM",
            ),
            CompanyRecord(key="nvidia", name="NVIDIA Corporation", ticker="NVDA"),
            CompanyRecord(key="allstate", name="Allstate Corporation", ticker="ALL"),
            CompanyRecord(key="onsemi", name="ON Semiconductor Corporation", ticker="ON"),
        ]
    )


def edges(text: str, resolver: EntityResolver, filer: str = FILER):
    return [
        (r.source_entity_key, r.relationship, r.target_entity_key)
        for r in extract_relationships(text, filer, resolver)
    ]


# ------------------------------------------------------------------ rules
def test_every_rule_declares_a_party_window() -> None:
    """A rule with no party group would resolve entities over the whole sentence."""
    for rule in RULES:
        assert "party" in rule.pattern.groupindex
        assert 0.0 < rule.weight <= 1.0
        assert 0.0 < rule.confidence <= 1.0


def test_supplier_list_becomes_inbound_supply_edges(resolver: EntityResolver) -> None:
    found = edges(
        "Our principal suppliers include Samsung Electronics and Taiwan Semiconductor "
        "Manufacturing Company.",
        resolver,
    )
    assert ("samsung", RelationshipType.SUPPLIES, FILER) in found
    assert ("tsmc", RelationshipType.SUPPLIES, FILER) in found


def test_purchase_from_points_supply_at_the_filer(resolver: EntityResolver) -> None:
    found = edges(
        "We purchase substantially all of our leading-edge wafers from Taiwan "
        "Semiconductor Manufacturing Company.",
        resolver,
    )
    assert ("tsmc", RelationshipType.SUPPLIES, FILER) in found


def test_customer_list_becomes_outbound_demand_edges(resolver: EntityResolver) -> None:
    found = edges("Our largest customers include Microsoft Corporation.", resolver)
    # The customer buys from the filer, so the filer is the *target* of BUYS_FROM.
    assert ("msft", RelationshipType.BUYS_FROM, FILER) in found


def test_revenue_concentration_is_a_customer_edge(resolver: EntityResolver) -> None:
    found = extract_relationships(
        "NVIDIA Corporation accounted for approximately 18% of our total net revenue.",
        FILER,
        resolver,
    )
    assert [(r.source_entity_key, r.relationship, r.target_entity_key) for r in found] == [
        ("nvidia", RelationshipType.BUYS_FROM, FILER)
    ]
    # Disclosed concentration is the strongest commercial linkage a filing states.
    assert found[0].weight == pytest.approx(0.85)


def test_competitor_edges_run_from_the_filer(resolver: EntityResolver) -> None:
    found = edges("The Company competes primarily with Microsoft Corporation.", resolver)
    assert (FILER, RelationshipType.COMPETES_WITH, "msft") in found


def test_dependency_is_recorded_as_its_own_edge_type(resolver: EntityResolver) -> None:
    found = edges("We depend heavily on NVIDIA Corporation for accelerator supply.", resolver)
    assert (FILER, RelationshipType.DEPENDS_ON, "nvidia") in found


def test_producer_edges_reach_concept_nodes(resolver: EntityResolver) -> None:
    found = extract_relationships(
        "We design and manufacture high-bandwidth memory and DRAM modules.", FILER, resolver
    )
    produced = {
        (r.target_entity_type, r.target_entity_key)
        for r in found
        if r.relationship is RelationshipType.PRODUCES
    }
    assert (EntityType.PRODUCT, "hbm") in produced
    assert (EntityType.PRODUCT, "dram") in produced


# ------------------------------------------------------------- precision
def test_negated_disclosure_produces_no_edge(resolver: EntityResolver) -> None:
    """'We no longer purchase from X' asserts the *absence* of the relationship."""
    assert edges("We no longer purchase display panels from Samsung Electronics.", resolver) == []
    assert edges("We do not compete with Microsoft Corporation.", resolver) == []


def test_clause_break_stops_a_window_running_into_the_next_claim(
    resolver: EntityResolver,
) -> None:
    """Without the clause guard, the supplier in the second clause is filed as a customer."""
    found = edges(
        "Our customers include Microsoft Corporation, and our suppliers include Samsung "
        "Electronics.",
        resolver,
    )
    assert ("msft", RelationshipType.BUYS_FROM, FILER) in found
    assert ("samsung", RelationshipType.SUPPLIES, FILER) in found
    assert ("samsung", RelationshipType.BUYS_FROM, FILER) not in found


def test_window_does_not_cross_a_sentence_boundary(resolver: EntityResolver) -> None:
    found = edges(
        "Our suppliers include Samsung Electronics. Microsoft Corporation is a customer "
        "of many firms.",
        resolver,
    )
    assert ("msft", RelationshipType.SUPPLIES, FILER) not in found


def test_the_filer_is_never_its_own_counterparty(resolver: EntityResolver) -> None:
    assert edges("Our suppliers include Apple Inc. subsidiaries.", resolver) == []


def test_common_word_tickers_do_not_become_edges(resolver: EntityResolver) -> None:
    """The C4 failure mode, re-checked at the relationship layer."""
    assert edges("Our suppliers include all of the vendors listed on Exhibit 21.", resolver) == []
    assert edges("We compete with on-premises deployments of similar tools.", resolver) == []


def test_a_document_without_a_filer_yields_nothing(resolver: EntityResolver) -> None:
    """First-person rules are meaningless without knowing who 'we' is."""
    assert extract_relationships("Our suppliers include Samsung Electronics.", "", resolver) == []


def test_unknown_counterparties_are_dropped_not_invented(resolver: EntityResolver) -> None:
    assert edges("Our suppliers include Northbridge Memory Corporation.", resolver) == []


def test_hard_wrapped_filings_still_match(resolver: EntityResolver) -> None:
    """Filing text is hard-wrapped; `.` in a rule span does not cross a newline."""
    wrapped = (
        "Our principal suppliers include\nSamsung Electronics and Taiwan\n"
        "Semiconductor Manufacturing Company."
    )
    assert ("samsung", RelationshipType.SUPPLIES, FILER) in edges(wrapped, resolver)


def test_excerpt_offsets_point_at_the_original_text(resolver: EntityResolver) -> None:
    text = "Preamble sentence. Our suppliers include Samsung Electronics."
    found = extract_relationships(text, FILER, resolver)
    assert found
    item = found[0]
    assert text[item.excerpt_start : item.excerpt_end].strip() == item.excerpt


def test_edge_confidence_is_capped_by_the_mention(resolver: EntityResolver) -> None:
    """A shakier mention must not inherit the rule's full confidence."""
    strong = extract_relationships(
        "Our suppliers include Samsung Electronics.", FILER, resolver
    )[0]
    assert strong.confidence < 0.80  # rule 0.80 x mention confidence, never above the rule


# ------------------------------------------------- measured, not asserted
#: Sentences with the edges a careful reader would draw from them. Negative cases carry an
#: empty expectation, so a spurious edge counts against precision rather than being ignored.
LABELLED: tuple[tuple[str, tuple[tuple[str, RelationshipType, str], ...]], ...] = (
    (
        "Our principal suppliers include Samsung Electronics.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "We purchase memory components from Samsung Electronics and NVIDIA Corporation.",
        (
            ("samsung", RelationshipType.SUPPLIES, FILER),
            ("nvidia", RelationshipType.SUPPLIES, FILER),
        ),
    ),
    (
        "Certain components are manufactured for us by Taiwan Semiconductor Manufacturing "
        "Company.",
        (("tsmc", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "The Company's sole source supplier for these assemblies is Samsung Electronics.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "Microsoft Corporation accounted for 11.4% of our consolidated net revenues.",
        (("msft", RelationshipType.BUYS_FROM, FILER),),
    ),
    (
        "Our significant customers include NVIDIA Corporation.",
        (("nvidia", RelationshipType.BUYS_FROM, FILER),),
    ),
    (
        "We sell subscription services to Microsoft Corporation under a multi-year "
        "agreement.",
        (("msft", RelationshipType.BUYS_FROM, FILER),),
    ),
    (
        "We compete directly with NVIDIA Corporation and Microsoft Corporation.",
        (
            (FILER, RelationshipType.COMPETES_WITH, "nvidia"),
            (FILER, RelationshipType.COMPETES_WITH, "msft"),
        ),
    ),
    (
        "Our principal competitors include ON Semiconductor Corporation.",
        ((FILER, RelationshipType.COMPETES_WITH, "onsemi"),),
    ),
    (
        "We face increasing competition from Microsoft Corporation in this segment.",
        ((FILER, RelationshipType.COMPETES_WITH, "msft"),),
    ),
    (
        "We rely substantially on Taiwan Semiconductor Manufacturing Company for capacity.",
        ((FILER, RelationshipType.DEPENDS_ON, "tsmc"),),
    ),
    # --- phrasings added to the rule set after they were first missed ----
    (
        "We obtain certain components from single or limited sources of supply, including "
        "Samsung Electronics.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "The Company purchases raw materials from a number of vendors, including Samsung "
        "Electronics.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "In fiscal 2025, sales to NVIDIA Corporation represented 14% of net revenues.",
        (("nvidia", RelationshipType.BUYS_FROM, FILER),),
    ),
    (
        "Samsung Electronics is our largest supplier of memory components.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "We have entered into a long-term supply agreement with Taiwan Semiconductor "
        "Manufacturing Company.",
        (("tsmc", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "Our products compete against those offered by NVIDIA Corporation.",
        ((FILER, RelationshipType.COMPETES_WITH, "nvidia"),),
    ),
    (
        "A substantial portion of our net revenues was derived from sales to Microsoft "
        "Corporation.",
        (("msft", RelationshipType.BUYS_FROM, FILER),),
    ),
    # --- phrasings added to the rule set after a held-out run missed them --
    (
        "Component shortages at Samsung Electronics adversely affected our production.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "Approximately 30% of our purchases were made from Taiwan Semiconductor "
        "Manufacturing Company.",
        (("tsmc", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "We outsource final assembly to Samsung Electronics.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "Our foundry partner, Taiwan Semiconductor Manufacturing Company, fabricates our "
        "chips.",
        (("tsmc", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "Microsoft Corporation and NVIDIA Corporation are among our principal competitors.",
        (
            (FILER, RelationshipType.COMPETES_WITH, "msft"),
            (FILER, RelationshipType.COMPETES_WITH, "nvidia"),
        ),
    ),
    (
        "We license certain patents to Microsoft Corporation.",
        (("msft", RelationshipType.BUYS_FROM, FILER),),
    ),
    # --- negatives: nothing should be produced ---------------------------
    ("Our board includes a former executive of Microsoft Corporation.", ()),
    ("The market for our products is highly competitive.", ()),
    ("We maintain deposit accounts with several large financial institutions.", ()),
    ("All of our manufacturing facilities are insured.", ()),
    ("The key driver was increasing demand on strong server orders.", ()),
    ("We no longer source these assemblies from Samsung Electronics.", ()),
    ("We terminated our supply agreement with Samsung Electronics in 2024.", ()),
    ("Revenue increased 12% compared with the prior year.", ()),
    ("Our suppliers include a number of privately held vendors.", ()),
    ("Microsoft Corporation announced a new data centre in Iowa.", ()),
    ("We compete with a large number of companies, many of which are larger than us.", ()),
    # Microsoft is a customer of our suppliers here, not a supplier of ours.
    ("Our suppliers include vendors that also supply Microsoft Corporation.", ()),
    # A competitor of our supplier is not, on this sentence alone, a competitor of ours.
    ("Our suppliers include vendors that compete with NVIDIA Corporation.", ()),
)


#: Phrasings kept out of the rule set while it was written, so the numbers below say what
#: the extractor does on filing language it was not fitted to rather than how well it
#: reproduces its own design set.
#:
#: The discipline this file follows, and that any change to it must keep: a sentence in the
#: design set above may be used to write or repair a rule; a sentence here may not. When a
#: miss here is worth fixing, the sentence **moves into the design set first** and the rule
#: is written against it there — which is exactly how the seven "added after they were first
#: missed" entries above got there. Adding a rule for a sentence that stays in this set
#: turns the figure below into a measurement of nothing.
HELD_OUT: tuple[tuple[str, tuple[tuple[str, RelationshipType, str], ...]], ...] = (
    (
        "Substantially all of our wafers are fabricated by Taiwan Semiconductor "
        "Manufacturing Company.",
        (("tsmc", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "NVIDIA Corporation, our largest customer, renewed its master agreement in March.",
        (("nvidia", RelationshipType.BUYS_FROM, FILER),),
    ),
    (
        "Sales through Microsoft Corporation represented a growing share of our revenue.",
        (("msft", RelationshipType.BUYS_FROM, FILER),),
    ),
    (
        "We anticipate purchasing additional wafer capacity from Taiwan Semiconductor "
        "Manufacturing Company.",
        (("tsmc", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "The Company's competitors in the accelerator market include NVIDIA Corporation.",
        ((FILER, RelationshipType.COMPETES_WITH, "nvidia"),),
    ),
    (
        "A single vendor, Samsung Electronics, provides the majority of our DRAM.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    (
        "We have a multi-year commitment to purchase memory from Samsung Electronics.",
        (("samsung", RelationshipType.SUPPLIES, FILER),),
    ),
    # --- negatives ---------------------------------------------------------
    ("Our results may be affected by pricing actions taken by NVIDIA Corporation.", ()),
    ("Microsoft Corporation holds a minority interest in one of our joint ventures.", ()),
    ("We paid $4.2 million in fees to our independent registered accounting firm.", ()),
    ("Demand from data centre customers increased during the year.", ()),
    ("We compete for talent with other technology companies.", ()),
)


#: Sentences taken verbatim from REAL SEC filings in a live pipeline run (2026-09-09,
#: 72 documents over AAPL/MSFT/NVDA). These are worth more than every invented sentence in
#: this file put together: they are the language the extractor actually meets. Both defects
#: they exposed are fixed and pinned below.
FIELD: tuple[tuple[str, str, tuple[tuple[str, RelationshipType, str], ...]], ...] = (
    # nvda-20260125.htm — three suppliers disclosed in one sentence. The party window used
    # to stop at the period in "Inc.", so only the FIRST was ever extracted.
    (
        "We purchase memory from SK Hynix Inc., Micron Technology, Inc., and Samsung.",
        "nvda",
        (
            ("sec-0002120882", RelationshipType.SUPPLIES, "nvda"),
            ("mu", RelationshipType.SUPPLIES, "nvda"),
        ),
    ),
    # msft-20250930.htm — revenue-recognition boilerplate that produced a confident,
    # entirely fictitious customer edge: the rule keyed on the "to" in "need to determine",
    # and the resolver matched the bare lower-case words "discount" and "various" to
    # issuers whose names are those words.
    (
        "We use a range of amounts to estimate SSP when we sell each of the products and "
        "services separately and need to determine whether there is a discount to be "
        "allocated based on the relative SSP of the various products and services.",
        "msft",
        (),
    ),
)


@pytest.fixture
def field_resolver() -> EntityResolver:
    """The issuers involved in the field sentences, named as SEC names them."""
    return EntityResolver(
        [
            CompanyRecord(key="nvda", name="NVIDIA CORP", ticker="NVDA"),
            CompanyRecord(key="msft", name="MICROSOFT CORP", ticker="MSFT"),
            CompanyRecord(key="mu", name="MICRON TECHNOLOGY INC", ticker="MU"),
            CompanyRecord(key="sec-0002120882", name="SK hynix Inc.", ticker=None),
            # Issuers whose names are ordinary English words. The real 8,010-issuer
            # universe is full of these; they are why the casing gate exists.
            CompanyRecord(key="various", name="Various Inc.", ticker="VARI"),
            CompanyRecord(key="discount", name="Discount Holdings", ticker="DISC"),
            CompanyRecord(key="range", name="Range Resources Corporation", ticker="RRC"),
        ]
    )


def test_all_three_suppliers_survive_an_abbreviation_period(
    field_resolver: EntityResolver,
) -> None:
    """`[^.;]` windows used to die on "Inc.", losing every supplier after the first."""
    found = extract_relationships(FIELD[0][0], FIELD[0][1], field_resolver)
    assert {r.source_entity_key for r in found} == {"sec-0002120882", "mu"}


def test_accounting_boilerplate_produces_no_customer_edge(
    field_resolver: EntityResolver,
) -> None:
    """The live false positive, pinned. Two guards must each be sufficient alone."""
    assert extract_relationships(FIELD[1][0], FIELD[1][1], field_resolver) == []
    # The resolver alone must also refuse the bare lower-case words.
    assert field_resolver.resolve(FIELD[1][0]) == []


def test_field_sentences_score_clean(field_resolver: EntityResolver) -> None:
    """Scored per filer, because these sentences come from different companies' filings."""
    scored = [
        _score(((sentence, expected),), field_resolver, filer=filer)
        for sentence, filer, expected in FIELD
    ]
    totals = tuple(sum(s[i] for s in scored) for i in (2, 3, 4))
    spurious = [item for s in scored for item in s[5]]
    misses = [item for s in scored for item in s[6]]
    tp, fp, fn = totals
    precision = tp / (tp + fp or 1)
    recall = tp / (tp + fn or 1)
    print(
        f"\nfield set (real SEC filings): precision={precision:.3f} recall={recall:.3f} "
        f"(tp={tp} fp={fp} fn={fn})"
    )
    for sentence, extra in spurious:
        print(f"  FALSE POSITIVE {extra} <- {sentence}")
    for sentence, missed in misses:
        print(f"  MISSED         {missed} <- {sentence}")
    assert precision >= 0.99, f"spurious edges on real filings: {spurious}"
    assert recall >= 0.99, f"missed edges on real filings: {misses}"


def _score(
    labelled: tuple[tuple[str, tuple], ...],
    resolver: EntityResolver,
    filer: str = FILER,
) -> tuple[float, float, int, int, int, list, list]:
    true_positives = false_positives = false_negatives = 0
    misses: list[tuple[str, tuple]] = []
    spurious: list[tuple[str, tuple]] = []

    for sentence, expected in labelled:
        produced = {
            (r.source_entity_key, r.relationship, r.target_entity_key)
            for r in extract_relationships(sentence, filer, resolver)
            # Concept edges are scored separately; this set labels company-to-company edges.
            if r.target_entity_type is EntityType.COMPANY
        }
        wanted = set(expected)
        true_positives += len(produced & wanted)
        false_positives += len(produced - wanted)
        false_negatives += len(wanted - produced)
        if produced - wanted:
            spurious.append((sentence, tuple(produced - wanted)))
        if wanted - produced:
            misses.append((sentence, tuple(wanted - produced)))

    precision = true_positives / (true_positives + false_positives or 1)
    recall = true_positives / (true_positives + false_negatives or 1)
    return precision, recall, true_positives, false_positives, false_negatives, spurious, misses


def _report(name: str, scored: tuple) -> None:
    precision, recall, tp, fp, fn, spurious, misses = scored
    print(f"\n{name}: precision={precision:.3f} recall={recall:.3f} (tp={tp} fp={fp} fn={fn})")
    for sentence, extra in spurious:
        print(f"  FALSE POSITIVE {extra} <- {sentence}")
    for sentence, missed in misses:
        print(f"  MISSED         {missed} <- {sentence}")


def test_measured_precision_and_recall_on_the_design_set(resolver: EntityResolver) -> None:
    """Report the numbers, then hold them to a floor.

    This set is what the rules were fitted to, so a high score here is a regression guard,
    not a performance claim. The performance claim is the held-out test below.
    """
    scored = _score(LABELLED, resolver)
    _report("design set", scored)
    precision, recall, *_ , spurious, misses = scored
    # Precision is the hard floor: a wrong edge propagates through every traversal that
    # crosses it, and a reader has no way to tell it apart from a right one.
    assert precision >= 0.95, f"precision {precision:.3f}; spurious edges: {spurious}"
    assert recall >= 0.90, f"recall {recall:.3f}; missed edges: {misses}"


def test_measured_precision_and_recall_on_held_out_phrasings(resolver: EntityResolver) -> None:
    """What the extractor does on filing language it was never fitted to.

    Measured at the time of writing: **precision 1.00, recall 0.29** (2 of 7 relationships
    found, 0 spurious edges over 12 sentences). That recall is the honest coverage of a
    regex rule set on unseen phrasings, and it is the number that justifies the next step
    rather than an embarrassment to be tuned away — an LLM relationship extractor behind
    this same interface, scored on this same set, has something concrete to beat.

    The precision floor is not relaxed to match. Silence on an unfamiliar phrasing costs a
    missing edge; a wrong one propagates through every traversal that crosses it, and a
    reader cannot tell it from a right one.
    """
    scored = _score(HELD_OUT, resolver)
    _report("held-out set", scored)
    precision, recall, *_ , spurious, misses = scored
    assert precision >= 0.95, f"precision {precision:.3f}; spurious edges: {spurious}"
    assert recall >= 0.25, f"recall {recall:.3f}; missed edges: {misses}"


def test_held_out_set_has_not_been_fitted_to(resolver: EntityResolver) -> None:
    """A structural guard on the discipline the two sets depend on.

    Nothing stops a future change from quietly moving a held-out sentence into the design
    set to make a rule look better, but the two sets must at least stay disjoint, and the
    held-out set must stay large enough for its recall figure to mean anything.
    """
    design = {sentence for sentence, _ in LABELLED}
    held_out = {sentence for sentence, _ in HELD_OUT}
    assert not (design & held_out), "a sentence cannot be in both sets"
    assert len(held_out) >= 10
    assert sum(1 for _, expected in HELD_OUT if expected) >= 5
