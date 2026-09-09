"""Deterministic evidence extraction.

Turns document text into claim-bearing excerpts with a typed event classification.

Why rules and not a model (ADR-008): this step is span selection plus classification over a
closed label set. Rules are reproducible, auditable, free, instant, and — crucially — cannot
invent a sentence that is not in the document. The extractor's precision/recall is a known,
measurable quantity that the evaluation plan tracks; an LLM extractor can be introduced
behind this same interface once there is a labelled set to measure it against.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from marketradar.domain.enums import Direction, EventType

EXTRACTOR_NAME = "rule_extractor"
#: Bumped whenever extraction BEHAVIOUR changes, not only when this file does: the
#: version is the idempotency key, so leaving it alone after a fix makes every document
#: look already-done and the fix never reaches stored data. A bump retracts the previous
#: version's output (marketradar.ingestion.retraction) and re-extracts.
#: 1.1.0 — evidence from a filing falls back to the filer when the sentence names nobody.
#: 1.2.0 — an uncued ticker now requires the issuer's name in the same text, which
#: changes which company evidence is attributed to (HBM was resolving to a copper miner).
EXTRACTOR_VERSION = "1.2.0"


@dataclass(frozen=True)
class ExtractionRule:
    """One pattern that identifies a typed change in a sentence."""

    #: Stable identity of this rule, e.g. ``demand_acceleration#1``. Stored on every
    #: evidence item so a reader can see exactly which rule produced a claim, and so the
    #: (document, span, rule) triple is a natural key for idempotent re-extraction.
    rule_key: str
    pattern: re.Pattern[str]
    event_type: EventType
    direction: Direction
    magnitude: float
    confidence: float
    claim: str


_RULE_COUNTS: defaultdict[EventType, int] = defaultdict(int)


def _rule(
    regex: str,
    event_type: EventType,
    magnitude: float,
    confidence: float,
    claim: str,
    direction: Direction = Direction.POSITIVE,
) -> ExtractionRule:
    _RULE_COUNTS[event_type] += 1
    return ExtractionRule(
        rule_key=f"{event_type.value.lower()}#{_RULE_COUNTS[event_type]}",
        pattern=re.compile(regex, re.IGNORECASE),
        event_type=event_type,
        direction=direction,
        magnitude=magnitude,
        confidence=confidence,
        claim=claim,
    )


# Ordered by specificity: the first match on a sentence wins, so "cannot meet demand"
# classifies as a supply constraint rather than as generic demand language.
RULES: tuple[ExtractionRule, ...] = (
    # --- supply constraint (strongest form of demand evidence) ---------
    _rule(r"(cannot|could not|unable to) (currently )?meet.{0,30}(demand|delivery|schedule)",
          EventType.SUPPLY_CONSTRAINT, 0.95, 0.90,
          "Supplier states it cannot meet current demand"),
    _rule(r"capacity.{0,40}(substantially |largely |fully )?committed",
          EventType.SUPPLY_CONSTRAINT, 0.80, 0.85,
          "Forward capacity is reported as committed"),
    _rule(r"(lead times?).{0,30}(extend|lengthen)|extended lead times?",
          EventType.SUPPLY_CONSTRAINT, 0.65, 0.80,
          "Lead times are reported as extending"),
    _rule(r"(tighten|constrain|shortage).{0,30}(availability|supply)|"
          r"(availability|supply).{0,30}(tighten|constrain)",
          EventType.SUPPLY_CONSTRAINT, 0.70, 0.75,
          "Supply availability is reported as tightening"),
    _rule(r"(procurement|supply).{0,30}(is |as )?a constraint",
          EventType.SUPPLY_CONSTRAINT, 0.75, 0.80,
          "Input procurement is described as a constraint on output"),
    # --- demand -------------------------------------------------------
    _rule(r"demand.{0,40}(is |are |has been )?accelerat",
          EventType.DEMAND_ACCELERATION, 0.85, 0.85,
          "Demand is described as accelerating"),
    _rule(r"(increasing|increased|stronger|higher|growing) demand|"
          r"demand.{0,60}(increas|grow|strengthen)",
          EventType.DEMAND_ACCELERATION, 0.65, 0.75,
          "Demand is described as increasing"),
    _rule(r"(order (backlog|intake)|backlog|orders).{0,30}(increase|grew|grow|extend|rose)",
          EventType.DEMAND_ACCELERATION, 0.70, 0.80,
          "Order backlog or intake is reported as increasing"),
    _rule(r"(shipments|volumes).{0,25}(continued to grow|increase|grew|rose)",
          EventType.DEMAND_ACCELERATION, 0.55, 0.70,
          "Shipment volumes are reported as growing"),
    _rule(r"(increased|increasing|greater).{0,30}(forward )?(purchase|procurement) commitments",
          EventType.DEMAND_ACCELERATION, 0.70, 0.80,
          "Buyers are increasing forward purchase commitments"),
    _rule(r"(content|requirement).{0,30}per.{0,25}(server|unit|system).{0,30}"
          r"(increase|risen|grown|higher)",
          EventType.DEMAND_ACCELERATION, 0.75, 0.80,
          "Content per unit is reported as increasing"),
    _rule(r"demand.{0,30}(remains? soft|weak|declin|deteriorat)|"
          r"(soft|weak|declining) demand",
          EventType.DEMAND_WEAKNESS, 0.65, 0.80,
          "Demand is described as soft or weakening"),
    # --- pricing ------------------------------------------------------
    _rule(r"(contract )?pricing.{0,25}(increase|rose|improved|higher|moves? higher)|"
          r"prices?.{0,25}(increase|rose|rising|higher)",
          EventType.PRICING_INCREASE, 0.70, 0.80,
          "Pricing is reported as increasing"),
    _rule(r"(unit values|prices?).{0,25}(creep|tick).{0,10}up",
          EventType.PRICING_INCREASE, 0.35, 0.55,
          "Prices are anecdotally reported as edging up"),
    _rule(r"(declared unit values|unit values).{0,20}(rose|increase)",
          EventType.PRICING_INCREASE, 0.60, 0.85,
          "Declared unit values are reported as rising"),
    _rule(r"pricing.{0,25}(declin|fell|lower|decrease)|prices?.{0,25}(declin|fell|decrease)",
          EventType.PRICING_DECREASE, 0.70, 0.80,
          "Pricing is reported as declining"),
    # --- inventory ----------------------------------------------------
    _rule(r"inventor(y|ies).{0,30}(declin|draw down|drew down|fell|below the normal)|"
          r"(declin|draw down).{0,20}inventor",
          EventType.INVENTORY_DECLINE, 0.70, 0.80,
          "Inventory is reported as declining"),
    _rule(r"inventor(y|ies).{0,25}(increase|rose|build|built)",
          EventType.INVENTORY_BUILD, 0.60, 0.80,
          "Inventory is reported as building"),
    # --- capital -----------------------------------------------------
    _rule(r"(increas\w*).{0,35}capital expenditure|"
          r"capital expenditure.{0,25}(increase|will increase|rise)",
          EventType.CAPEX_INCREASE, 0.75, 0.85,
          "Capital expenditure is being increased"),
    _rule(r"(reduc\w*|cut\w*|lower\w*).{0,30}capital expenditure",
          EventType.CAPEX_DECREASE, 0.70, 0.85,
          "Capital expenditure is being reduced"),
    _rule(r"(capacity expansion|expand.{0,15}capacity|capacity additions|"
          r"adding manufacturing capacity|expansion of production capacity|"
          r"accelerated.{0,25}capacity expansion)",
          EventType.CAPACITY_EXPANSION, 0.70, 0.80,
          "Production capacity is being expanded"),
    _rule(r"(construction of|begin construction|additional facility|new fab)",
          EventType.CAPACITY_EXPANSION, 0.65, 0.75,
          "A new production facility is being built"),
    # --- other -------------------------------------------------------
    _rule(r"(multi-year )?supply agreement|supply contract|long-term agreement",
          EventType.CONTRACT_AWARD, 0.60, 0.85,
          "A supply agreement was announced"),
    _rule(r"(shares|stock).{0,30}(have moved|rose|rallied)|valuations? already (embed|price)",
          EventType.MARKET_REACTION, 0.55, 0.70,
          "Market prices are reported to have already moved"),
)

#: Sentences matching these are explicitly *stable*; they must not produce change events.
#: Without this, "demand was steady" would match a demand rule and manufacture a trend.
STABILITY_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(broadly )?(stable|flat|unchanged|little changed|in line with expectations)\b",
        r"\bwithin the normal (historical )?range\b",
        r"\bno (notable |immediate )?(shortage|change|supplier reported)\b",
        r"\bconsistent with (the )?prior\b",
        r"\bproceeding on\b.{0,30}\bschedules?\b",
        r"\bmaintain(s|ed)? a neutral\b",
        r"\bdescribed as steady\b",
        r"\bremained steady\b",
    )
)

#: Subject lexicon. Maps vocabulary to the subject a signal is tracked against.
SUBJECT_LEXICON: dict[str, tuple[str, ...]] = {
    "memory": ("memory", "dram", "hbm", "nand", "high-bandwidth", "high bandwidth", "module"),
    "ai_infrastructure": (
        "artificial intelligence",
        "ai server",
        "ai servers",
        "ai accelerator",
        "ai capacity",
        "ai infrastructure",
        "accelerator",
        "hyperscale",
        "cloud customers",
    ),
    "semiconductor_equipment": (
        "equipment",
        "lithography",
        "deposition",
        "test systems",
        "packaging",
        "tools",
        "wafer",
    ),
}

#: Sentences that argue about what *might* or *historically did* happen. These are genuine
#: evidence — the bear case is built from them — but they are not observations that a change
#: occurred, so they must not feed the signal engine. Without this, "we see risk that supply
#: expands faster than demand" would be counted as evidence of demand acceleration.
FORWARD_LOOKING_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bwe (caution|see risk|expect|forecast|estimate|believe|would need|maintain)\b",
        r"\brisk that\b",
        r"\bcould (be|produce|lead|result|just)\b",
        r"\bmay (be|produce|remain|not|represent)\b",
        r"\bhistorically\b",
        r"\bin previous cycles\b",
        r"\bhave been followed by\b",
        r"\bwe see read-across\b",
        r"\b(seems?|sounds?) like\b",
        r"\bhard to say\b",
        r"\bwould need\b",
    )
)

#: At most this many distinct claims are taken from one sentence.
MAX_CLAIMS_PER_SENTENCE = 3

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ExtractedEvidence:
    """One extracted claim, with the exact span it came from."""

    claim: str
    excerpt: str
    excerpt_start: int
    excerpt_end: int
    #: ``None`` for forward-looking or historical statements: they are evidence, but they
    #: are not observations of a change and never contribute to a signal.
    event_type: EventType | None
    direction: Direction
    magnitude: float
    confidence: float
    subject_key: str | None
    entity_hint: str | None
    rule_key: str
    is_forward_looking: bool = False


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split into ``(start, end, sentence)`` triples, preserving character offsets.

    Offsets are kept so an evidence item can point at the exact span of the source
    document, which is what makes "show me where this came from" resolvable to a
    highlightable region rather than to a whole article.
    """
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


def detect_subject(
    sentence: str,
    fallback_hints: tuple[str, ...] = (),
    vocabulary: dict[str, tuple[str, ...]] | None = None,
) -> str | None:
    """Which tracked subject a sentence is about.

    ``vocabulary`` maps a subject key to the terms that identify it. It defaults to the
    built-in lexicon, but the pipeline passes **discovered** subjects instead: the built-in
    list names three topics somebody typed in, and a system that can only recognise what it
    was taught cannot discover anything (audit C6).

    Falls back to the provider's document-level hints when the sentence itself is
    non-specific (pronouns, "the company", ...), which is common in filings.
    """
    vocabulary = SUBJECT_LEXICON if vocabulary is None else vocabulary
    lowered = sentence.lower()
    best: tuple[int, int, str] | None = None
    for subject, terms in vocabulary.items():
        hits = sum(1 for term in terms if term in lowered)
        if not hits:
            continue
        # A longer matched term is more specific evidence of the subject than a short one,
        # so it breaks ties: "high bandwidth memory" beats a bare "memory".
        longest = max((len(term) for term in terms if term in lowered), default=0)
        ranked = (hits, longest, subject)
        if best is None or ranked > best:
            best = ranked
    if best:
        return best[2]
    for hint in fallback_hints:
        for subject, terms in vocabulary.items():
            if hint.lower() in terms or hint.lower() == subject:
                return subject
    return None


def is_stability_statement(sentence: str) -> bool:
    """True when a sentence explicitly asserts that nothing changed."""
    return any(pattern.search(sentence) for pattern in STABILITY_PATTERNS)


def is_forward_looking(sentence: str) -> bool:
    """True for speculation, projection or historical analogy rather than observation."""
    return any(pattern.search(sentence) for pattern in FORWARD_LOOKING_PATTERNS)


def extract(
    text: str,
    resolve_entity: Callable[[str], str | None] | None = None,
    subject_hints: tuple[str, ...] = (),
    vocabulary: dict[str, tuple[str, ...]] | None = None,
) -> list[ExtractedEvidence]:
    """Extract typed, span-anchored evidence from a document body.

    One sentence may carry several distinct claims ("demand is accelerating **and** capacity
    is committed"), so several rules may fire — but only the first rule per event type, so a
    single statement cannot inflate the evidence count for the same underlying change.

    ``resolve_entity`` maps a sentence to a company key, or to ``None`` when no company is
    confidently identified. It is a function rather than a lexicon so the extractor stays
    independent of how resolution works; the substring lexicon it replaced attributed
    evidence containing the word "all" to Allstate.
    """
    results: list[ExtractedEvidence] = []

    for start, end, sentence in split_sentences(text):
        if not sentence:
            continue
        # Real filings are hard-wrapped, so a sentence routinely contains newlines. Every
        # rule uses `.{0,N}` spans, and `.` excludes newlines — matching the raw sentence
        # silently dropped any claim whose span crossed a line break. Matching is done on a
        # whitespace-collapsed copy; the original is kept for the excerpt and its offsets,
        # which must still point at the real document.
        matchable = " ".join(sentence.split())
        if is_stability_statement(matchable):
            continue
        speculative = is_forward_looking(matchable)
        entity_hint = resolve_entity(matchable) if resolve_entity else None

        seen_types: set[EventType] = set()
        for rule in RULES:
            if len(seen_types) >= MAX_CLAIMS_PER_SENTENCE:
                break
            if rule.event_type in seen_types or not rule.pattern.search(matchable):
                continue
            seen_types.add(rule.event_type)
            results.append(
                ExtractedEvidence(
                    claim=(
                        f"Forward-looking or historical statement regarding: {rule.claim.lower()}"
                        if speculative
                        else rule.claim
                    ),
                    excerpt=sentence,
                    excerpt_start=start,
                    excerpt_end=end,
                    # A projection is evidence, not an observed event.
                    event_type=None if speculative else rule.event_type,
                    direction=Direction.NEUTRAL if speculative else rule.direction,
                    magnitude=0.0 if speculative else rule.magnitude,
                    confidence=round(rule.confidence * 0.7, 3) if speculative else rule.confidence,
                    subject_key=detect_subject(matchable, subject_hints, vocabulary),
                    entity_hint=entity_hint,
                    rule_key=rule.rule_key,
                    is_forward_looking=speculative,
                )
            )
    return results
