"""Relationship extraction: value-chain edges read out of filing text.

The Cycle-1 audit finding (C5) was blunt: every edge in ``entity_relationships`` had been
hand-entered and **not one carried evidence**. A knowledge graph that a human typed is not
an intelligence system; it is a spreadsheet with a traversal on top. This module reads the
edges out of the documents instead, and every edge it produces points at the exact sentence
that asserts it.

How it works
------------
Filings are written from the filer's point of view: "we purchase", "our customers include",
"we compete with". That first-person frame is the lever. Each rule matches a disclosure cue
and captures a *party window* — the span of text that should name the counterparty — and the
entity resolver is then run **on that window only**, not on the whole sentence. Scoping
resolution to the window is what keeps precision up: a 10-K names dozens of companies, and
only the ones inside the window stand in the asserted relation to the filer.

Deliberate limits, so the output is not oversold:

* An edge is only produced when a counterparty resolves to a company **already known** to
  the system and is not the filer itself. Unknown counterparties are dropped, not invented.
* Ambiguous resolutions are dropped. A wrong edge propagates through every traversal that
  crosses it, so a missing edge is much cheaper than a wrong one.
* Rules are patterns over a closed set of disclosure phrasings, so recall is bounded by the
  phrasings listed here. This is measurable, and measured, rather than assumed.
* ``weight`` is a per-rule structural prior, not an estimate of trade volume. Nothing in a
  filing states how much of a supplier's revenue a relationship represents.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from marketradar.domain.enums import EntityType, RelationshipType
from marketradar.entities.resolver import EntityResolver

RELATIONSHIP_EXTRACTOR_NAME = "rule_relationship_extractor"
RELATIONSHIP_EXTRACTOR_VERSION = "rel-1.0.0"

#: Which side of the edge the filer occupies.
FilerRole = Literal["source", "target"]

#: How many counterparties one sentence may assert. Enumerations in filings are short;
#: a longer list is a sign the party window has over-run its clause.
MAX_PARTIES_PER_SENTENCE = 6

#: A resolved counterparty below this confidence is not worth an edge.
MIN_PARTY_CONFIDENCE = 0.6


@dataclass(frozen=True)
class RelationshipRule:
    """One disclosure phrasing that asserts a relationship between the filer and a party."""

    rule_key: str
    #: Must contain exactly one named group, ``party``: the span searched for counterparties.
    pattern: re.Pattern[str]
    relationship: RelationshipType
    filer_role: FilerRole
    #: Structural prior on the strength of the linkage (0..1), multiplied along a path.
    weight: float
    #: How sure the phrasing itself makes us that the relationship exists.
    confidence: float
    claim: str


def _rule(
    rule_key: str,
    regex: str,
    relationship: RelationshipType,
    filer_role: FilerRole,
    weight: float,
    confidence: float,
    claim: str,
) -> RelationshipRule:
    pattern = re.compile(regex, re.IGNORECASE)
    if "party" not in pattern.groupindex:
        raise ValueError(f"Relationship rule {rule_key} has no 'party' group")
    return RelationshipRule(
        rule_key=rule_key,
        pattern=pattern,
        relationship=relationship,
        filer_role=filer_role,
        weight=weight,
        confidence=confidence,
        claim=claim,
    )


#: The party window stops at clause-ending punctuation so one rule cannot reach into the
#: next clause and mis-attribute the company named there.
_W = r"[^.;]{0,180}"

RULES: tuple[RelationshipRule, ...] = (
    # --- inbound: someone supplies the filer ---------------------------------
    _rule(
        "sole_source",
        r"\b(?:sole|single|only)[- ]?(?:source|sourced|supplier|provider)\b[^.;]{0,60}?"
        rf"\b(?:is|are|from|by|:)\s*(?P<party>{_W})",
        RelationshipType.SUPPLIES,
        "target",
        0.85,
        0.80,
        "Filing identifies a sole or single-source supplier",
    ),
    _rule(
        "suppliers_include",
        r"\b(?:our|its|the Company's)\s+(?:principal\s+|primary\s+|key\s+|major\s+|main\s+"
        r"|significant\s+|largest\s+|current\s+)?"
        r"(?:suppliers?|vendors?|foundries|subcontractors?)\b[^.;]{0,40}?\s"
        rf"(?:include|includes|are|is|:)\s*(?P<party>{_W})",
        RelationshipType.SUPPLIES,
        "target",
        0.60,
        0.80,
        "Filing names a supplier of the filer",
    ),
    _rule(
        "purchase_from",
        r"\b(?:we|the Company|the Registrant)\s+(?:currently\s+|primarily\s+|also\s+"
        r"|principally\s+)?(?:purchase|purchases|buy|buys|procure|procures|source|sources"
        r"|obtain|obtains|license|licenses)\b[^.;]{0,100}?"
        rf"\bfrom\s+(?P<party>{_W})",
        RelationshipType.SUPPLIES,
        "target",
        0.60,
        0.75,
        "Filer states that it purchases from the named party",
    ),
    _rule(
        "supplied_by",
        r"\b(?:supplied|manufactured|produced|fabricated|assembled|fulfilled|sourced"
        r"|purchased|procured|licensed|obtained)\s+"
        rf"(?:for us\s+|exclusively\s+|solely\s+|primarily\s+)?(?:by|from)\s+(?P<party>{_W})",
        RelationshipType.SUPPLIES,
        "target",
        0.60,
        0.70,
        "Filing states that an input is supplied by the named party",
    ),
    _rule(
        "our_purchases_from",
        r"\b(?:our|its|the Company's)\s+(?:total\s+|annual\s+|aggregate\s+)?"
        r"(?:purchases|procurement|sourcing|orders)\b[^.;]{0,60}?"
        rf"\bfrom\s+(?P<party>{_W})",
        RelationshipType.SUPPLIES,
        "target",
        0.60,
        0.75,
        "Filing attributes the filer's purchases to the named party",
    ),
    _rule(
        "outsourced_to",
        r"\b(?:we|the Company)\s+(?:currently\s+|primarily\s+)?"
        r"(?:outsource|outsources|subcontract|subcontracts|contract|contracts)\b"
        rf"[^.;]{{0,80}}?\bto\s+(?P<party>{_W})",
        RelationshipType.SUPPLIES,
        "target",
        0.60,
        0.70,
        "Filer states that it outsources work to the named party",
    ),
    _rule(
        "named_partner",
        r"\b(?:our|its|the Company's)\s+(?:foundry|manufacturing|supply|assembly|contract"
        rf"|packaging|logistics)\s+partners?,?\s+(?P<party>{_W})",
        RelationshipType.SUPPLIES,
        "target",
        0.60,
        0.70,
        "Filing names a manufacturing or supply partner of the filer",
    ),
    _rule(
        "is_our_supplier",
        r"(?P<party>[^.;]{0,120}?)\s+(?:is|are|remains|remain|has been|have been)\s+"
        r"(?:our|its|the Company's)\s+(?:largest\s+|principal\s+|primary\s+|sole\s+|key\s+"
        r"|main\s+|significant\s+|only\s+)?"
        r"(?:suppliers?|vendors?|foundries|foundry|manufacturers?|subcontractors?)\b",
        RelationshipType.SUPPLIES,
        "target",
        0.60,
        0.80,
        "Filing states that the named party is a supplier of the filer",
    ),
    _rule(
        "supply_agreement_with",
        r"\b(?:supply|manufacturing|foundry|purchase|procurement)\s+"
        rf"(?:agreement|agreements|arrangement|arrangements|contract|contracts)\s+with\s+(?P<party>{_W})",
        RelationshipType.SUPPLIES,
        "target",
        0.60,
        0.70,
        "Filing discloses a supply agreement with the named party",
    ),
    _rule(
        "depends_on",
        r"\b(?:we|the Company)\s+(?:ha(?:ve|s)\s+)?(?:historically\s+|long\s+)?"
        r"(?:depend|depends|depended|rely|relies|relied)\s+"
        r"(?:heavily\s+|substantially\s+|primarily\s+|significantly\s+)?(?:up)?on\s+"
        rf"(?P<party>{_W})",
        RelationshipType.DEPENDS_ON,
        "source",
        0.70,
        0.70,
        "Filer states a dependency on the named party",
    ),
    # --- outbound: the filer supplies someone --------------------------------
    _rule(
        "revenue_concentration",
        r"(?P<party>[^.;]{0,120}?)\s+(?:accounted for|represented|comprised|generated)\s+"
        r"(?:approximately\s+|about\s+|more than\s+)?\d{1,3}(?:\.\d+)?\s*%\s+of\s+"
        r"(?:our|the Company's|total|net|consolidated)[^.;]{0,40}?(?:revenue|revenues|sales)",
        RelationshipType.BUYS_FROM,
        "target",
        0.85,
        0.85,
        "Filing discloses a customer revenue concentration",
    ),
    _rule(
        "customers_include",
        r"\b(?:our|its|the Company's)\s+(?:significant\s+|principal\s+|largest\s+|major\s+"
        r"|key\s+|primary\s+)?(?:customers?|resellers?|distributors?)\b[^.;]{0,40}?\s"
        rf"(?:include|includes|are|is|:)\s*(?P<party>{_W})",
        RelationshipType.BUYS_FROM,
        "target",
        0.60,
        0.80,
        "Filing names a customer of the filer",
    ),
    _rule(
        "is_our_customer",
        r"(?P<party>[^.;]{0,120}?)\s+(?:is|are|remains|remain|has been|have been)\s+"
        r"(?:our|its|the Company's)\s+(?:largest\s+|principal\s+|primary\s+|key\s+|main\s+"
        r"|significant\s+|only\s+)?"
        r"(?:customers?|resellers?|distributors?|licensees?)\b",
        RelationshipType.BUYS_FROM,
        "target",
        0.60,
        0.80,
        "Filing states that the named party is a customer of the filer",
    ),
    _rule(
        "sales_to",
        rf"\b(?:sales|shipments|revenues?)\s+to\s+(?P<party>{_W})",
        RelationshipType.BUYS_FROM,
        "target",
        0.55,
        0.70,
        "Filing attributes sales to the named party",
    ),
    _rule(
        "we_supply",
        r"\b(?:we|the Company)\s+(?:supply|supplies|sell|sells|ship|ships|provide|provides"
        r"|license|licenses|lease|leases|distribute|distributes)"
        rf"\b[^.;]{{0,100}}?\bto\s+(?P<party>{_W})",
        RelationshipType.BUYS_FROM,
        "target",
        0.60,
        0.75,
        "Filer states that it supplies the named party",
    ),
    # --- lateral --------------------------------------------------------------
    _rule(
        "compete_with",
        r"\b(?:we|the Company|our products|our offerings|our solutions|our services)\s+"
        r"(?:compete|competes)\s+"
        r"(?:primarily\s+|directly\s+|principally\s+|vigorously\s+)?(?:with|against)\s+"
        rf"(?P<party>{_W})",
        RelationshipType.COMPETES_WITH,
        "source",
        0.60,
        0.80,
        "Filer names a competitor",
    ),
    _rule(
        "competitors_include",
        r"\b(?:our|its|the Company's)\s+(?:principal\s+|primary\s+|main\s+|largest\s+"
        r"|significant\s+)?competitors?\b[^.;]{0,40}?\s(?:include|includes|are|is|:)\s*"
        rf"(?P<party>{_W})",
        RelationshipType.COMPETES_WITH,
        "source",
        0.60,
        0.80,
        "Filing names a competitor of the filer",
    ),
    _rule(
        "among_our_competitors",
        r"(?P<party>[^.;]{0,120}?)\s+(?:is|are|remains|remain)\s+(?:among|amongst|one of)\s+"
        r"(?:our|its|the Company's)\s+(?:principal\s+|primary\s+|main\s+|largest\s+"
        r"|significant\s+)?competitors?\b",
        RelationshipType.COMPETES_WITH,
        "source",
        0.60,
        0.80,
        "Filing names the party among the filer's competitors",
    ),
    _rule(
        "competition_from",
        rf"\bcompetition\s+from\s+(?P<party>{_W})",
        RelationshipType.COMPETES_WITH,
        "source",
        0.45,
        0.60,
        "Filing describes competition from the named party",
    ),
    _rule(
        "invests_in",
        r"\b(?:we|the Company)\s+(?:have\s+|has\s+)?(?:invested|acquired|hold|holds"
        rf"|purchased)\b[^.;]{{0,60}}?\bin\s+(?P<party>{_W})",
        RelationshipType.INVESTS_IN,
        "source",
        0.60,
        0.65,
        "Filer discloses an investment in the named party",
    ),
)

#: How far before a cue the negation guard reads. Long enough for "we terminated our supply
#: agreement with X", short enough not to reach an unrelated earlier clause.
_NEGATION_LOOKBACK = 64

#: Phrasings that negate or hypothesise the relationship the cue would otherwise assert.
#: Checked over the run-up to the cue and the cue itself, up to the party window.
_NEGATION = re.compile(
    r"\b(?:no longer|ceased|terminated|discontinued|does not|do not|did not|never"
    r"|other than|apart from|instead of|rather than)\b",
    re.IGNORECASE,
)

#: A new clause about the filer ends the enumeration. Without this, "our suppliers include
#: Alpha and our customers include Beta" would file Beta as a supplier.
_CLAUSE_BREAK = re.compile(
    r",?\s+(?:and|but|while|whereas|although)\s+(?:our|its|we\b|the Company)",
    re.IGNORECASE,
)

#: A relative clause changes what the window is talking about: in "our suppliers include
#: vendors that also supply Microsoft", Microsoft is a *customer of our suppliers*, not a
#: supplier of ours. The negative lookahead keeps the enumeration cue itself ("suppliers,
#: which include Alpha"), which is ordinary filing phrasing, from being cut off.
_RELATIVE_CLAUSE = re.compile(
    r"\s+(?:that|which|who|whose|whom)\s+(?!includ|are\b|is\b|we\b)",
    re.IGNORECASE,
)

#: Bootstrap vocabulary linking a producer to the concept nodes a theme is anchored to.
#: This is the one hand-maintained list left in the extraction path, and it is the subject
#: of the open discovery work (audit C6): until subjects are discovered from the corpus, a
#: company can only be linked to a concept this list already names.
CONCEPT_LEXICON: tuple[tuple[str, EntityType, str], ...] = (
    ("high-bandwidth memory", EntityType.PRODUCT, "hbm"),
    ("high bandwidth memory", EntityType.PRODUCT, "hbm"),
    ("hbm", EntityType.PRODUCT, "hbm"),
    ("dram", EntityType.PRODUCT, "dram"),
    ("nand", EntityType.PRODUCT, "nand"),
    ("flash memory", EntityType.PRODUCT, "nand"),
    ("ai accelerator", EntityType.TECHNOLOGY, "ai_server_deployment"),
    ("ai accelerators", EntityType.TECHNOLOGY, "ai_server_deployment"),
    ("ai server", EntityType.TECHNOLOGY, "ai_server_deployment"),
    ("ai servers", EntityType.TECHNOLOGY, "ai_server_deployment"),
    ("artificial intelligence infrastructure", EntityType.TECHNOLOGY, "ai_infrastructure"),
    ("ai infrastructure", EntityType.TECHNOLOGY, "ai_infrastructure"),
)

_PRODUCES_CUE = re.compile(
    r"\b(?:we|the Company)\s+(?:currently\s+|primarily\s+|also\s+)?"
    r"(?:design|designs|develop|develops|manufacture|manufactures|produce|produces"
    r"|make|makes|market|markets|fabricate|fabricates)\b(?P<party>[^.;]{0,140})",
    re.IGNORECASE,
)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ExtractedRelationship:
    """One relationship asserted by one sentence, with the span that asserts it."""

    source_entity_type: EntityType
    source_entity_key: str
    target_entity_type: EntityType
    target_entity_key: str
    relationship: RelationshipType
    weight: float
    confidence: float
    claim: str
    excerpt: str
    excerpt_start: int
    excerpt_end: int
    #: Unique within a document: the rule plus the ordinal of the party inside the window.
    rule_key: str
    #: The literal text that resolved to the counterparty, kept so a wrong edge is traceable
    #: to the words that produced it rather than requiring a re-run to explain.
    party_surface: str


def _truncate_window(window: str) -> str:
    """Cut the party window at the first clause boundary that changes the subject."""
    cuts = [
        match.start()
        for match in (_CLAUSE_BREAK.search(window), _RELATIVE_CLAUSE.search(window))
        if match is not None
    ]
    return window[: min(cuts)] if cuts else window


def _negated(text: str) -> bool:
    return bool(_NEGATION.search(text))


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, sentence)`` triples over ``text``, preserving character offsets."""
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for part in _SENTENCE_RE.split(text):
        if not part:
            continue
        start = text.find(part, cursor)
        if start < 0:  # pragma: no cover - defensive
            start = cursor
        end = start + len(part)
        spans.append((start, end, part.strip()))
        cursor = end
    return spans


def _concepts_in(window: str) -> list[tuple[EntityType, str, str]]:
    """Concept nodes named in a window, as ``(entity_type, key, surface)``."""
    lowered = window.lower()
    found: dict[tuple[EntityType, str], str] = {}
    for surface, entity_type, key in CONCEPT_LEXICON:
        # Word boundaries on both sides: "nand" must not match inside "understanding".
        if re.search(rf"(?<!\w){re.escape(surface)}(?!\w)", lowered):
            # Longest surface wins for a given node, so "high-bandwidth memory" is reported
            # rather than the "hbm" that a later entry would also match.
            current = found.get((entity_type, key))
            if current is None or len(surface) > len(current):
                found[(entity_type, key)] = surface
    return [(t, k, s) for (t, k), s in found.items()]


def extract_relationships(
    text: str,
    filer_key: str,
    resolver: EntityResolver,
    rules: Iterable[RelationshipRule] = RULES,
) -> list[ExtractedRelationship]:
    """Read filer-anchored relationships out of a document body.

    ``filer_key`` is the company key of the entity that filed the document; every edge has
    the filer on one side. A document with no identified filer yields nothing — the rules
    are first-person and mean nothing without knowing who "we" is.
    """
    if not filer_key:
        return []

    results: list[ExtractedRelationship] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for start, end, sentence in split_sentences(text):
        if not sentence:
            continue
        # Filings are hard-wrapped, and every rule uses `.{0,N}` spans, which do not cross a
        # newline. Matching runs on a whitespace-collapsed copy; the excerpt and its offsets
        # stay on the original so they still point at the real document.
        matchable = " ".join(sentence.split())

        for rule in rules:
            match = rule.pattern.search(matchable)
            if match is None:
                continue
            window = _truncate_window(match.group("party"))
            if not window.strip():
                continue
            # The cue may be negated by words that sit *before* it — "we terminated our
            # supply agreement with X" negates a cue that starts at "supply agreement" —
            # so the guard reads a bounded run-up as well as the cue itself.
            preamble = matchable[max(0, match.start() - _NEGATION_LOOKBACK) : match.start("party")]
            if _negated(preamble):
                continue

            ordinal = 0
            for party in resolver.resolve(window):
                if party.is_ambiguous or party.confidence < MIN_PARTY_CONFIDENCE:
                    continue
                if party.company_key == filer_key:
                    continue  # a filing names itself constantly; that is not an edge
                if ordinal >= MAX_PARTIES_PER_SENTENCE:
                    break

                if rule.filer_role == "source":
                    src_key, dst_key = filer_key, party.company_key
                else:
                    src_key, dst_key = party.company_key, filer_key

                identity = (
                    src_key,
                    dst_key,
                    rule.relationship.value,
                    rule.rule_key,
                    str(start),
                )
                if identity in seen:
                    continue
                seen.add(identity)

                results.append(
                    ExtractedRelationship(
                        source_entity_type=EntityType.COMPANY,
                        source_entity_key=src_key,
                        target_entity_type=EntityType.COMPANY,
                        target_entity_key=dst_key,
                        relationship=rule.relationship,
                        weight=rule.weight,
                        # The edge is only as good as the mention that produced it.
                        confidence=round(rule.confidence * party.confidence, 4),
                        claim=f"{rule.claim}: {party.surface}",
                        excerpt=sentence,
                        excerpt_start=start,
                        excerpt_end=end,
                        rule_key=f"{rule.rule_key}[{ordinal}]",
                        party_surface=party.surface,
                    )
                )
                ordinal += 1

        # --- producer edges: filer -> concept node -------------------------------
        produces = _PRODUCES_CUE.search(matchable)
        if produces is None:
            continue
        window = _truncate_window(produces.group("party"))
        if _negated(matchable[produces.start() : produces.start("party")]):
            continue
        for index, (entity_type, key, surface) in enumerate(sorted(_concepts_in(window))):
            identity = (filer_key, key, RelationshipType.PRODUCES.value, "produces", str(start))
            if identity in seen:
                continue
            seen.add(identity)
            results.append(
                ExtractedRelationship(
                    source_entity_type=EntityType.COMPANY,
                    source_entity_key=filer_key,
                    target_entity_type=entity_type,
                    target_entity_key=key,
                    relationship=RelationshipType.PRODUCES,
                    weight=0.60,
                    confidence=0.70,
                    claim=f"Filer states that it produces: {surface}",
                    excerpt=sentence,
                    excerpt_start=start,
                    excerpt_end=end,
                    rule_key=f"produces[{index}]",
                    party_surface=surface,
                )
            )

    return results


__all__ = [
    "CONCEPT_LEXICON",
    "MAX_PARTIES_PER_SENTENCE",
    "MIN_PARTY_CONFIDENCE",
    "RELATIONSHIP_EXTRACTOR_NAME",
    "RELATIONSHIP_EXTRACTOR_VERSION",
    "RULES",
    "ExtractedRelationship",
    "RelationshipRule",
    "extract_relationships",
    "split_sentences",
]
