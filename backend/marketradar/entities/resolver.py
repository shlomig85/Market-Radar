"""Company entity resolution.

Replaces the naive ``if surface.lower() in text.lower()`` substring match that the Cycle-1
audit showed attributing evidence to the wrong issuer:

    'Demand is accelerating on strong server orders.'  -> ON Semiconductor
    'All memory suppliers reported increasing demand.' -> Allstate
    'The key driver was increasing demand...'          -> KeyCorp

At ~10,000 US registrants this is not an edge case: a large share of tickers are ordinary
English words (ON, ALL, KEY, IT, CAT, SO, GO, FAST, LOVE, CAR, RUN), and substring matching
without word boundaries additionally matches inside longer words.

Design rules, each answering a specific failure:

* **Word boundaries always.** Prevents "CAT" matching "category".
* **Tickers must appear cased as tickers.** A bare ticker is accepted only when the token is
  genuinely upper-case in the source, because prose writes "all"/"on"/"key" in lower case.
  Short tickers additionally require an explicit cue (``NYSE:``, ``(TSLA)``) since an
  all-capitals headline could otherwise collide.
* **Ambiguity is preserved, not guessed.** A surface matching several issuers returns
  several candidates with confidences. First-match-wins is gone — it silently picked
  whichever company happened to be first in a dict.
* **Longest match wins on overlap**, so "Northbridge Memory Corp" beats "Northbridge".
* Every match records the surface, its kind and a confidence, so a mis-mapping is
  inspectable rather than invisible.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

RESOLVER_VERSION = "entity_resolver_v1"

SurfaceKind = Literal["legal_name", "short_name", "alias", "former_name", "ticker"]

#: Trailing corporate designators. Stripping them yields the name people actually write.
_SUFFIXES = (
    "incorporated", "inc", "corporation", "corp", "company", "co", "limited", "ltd",
    "plc", "llc", "lp", "holdings", "holding", "group", "sa", "nv", "ag", "se",
    "technologies", "technology",
)
_SUFFIX_RE = re.compile(
    r"[,\s]+(?:" + "|".join(_SUFFIXES) + r")\.?\s*$", re.IGNORECASE
)
_PUNCT = re.compile(r"[.,]")

#: A single token this common is never a usable company surface on its own.
_TOO_GENERIC = frozenset(
    [
        "a", "all", "american", "an", "and", "any", "are", "as", "at", "be", "best",
        "big", "by", "capital", "core", "energy", "fast", "first", "for", "free",
        "from", "general", "global", "good", "group", "holdings", "in", "industries",
        "international", "is", "key", "live", "national", "new", "next", "of", "on",
        "one", "open", "or", "partners", "real", "service", "services", "solutions",
        "some", "systems", "technologies", "technology", "the", "to", "top", "true",
        "two", "united", "was", "were", "with",
    ]
)

#: Cues that mark the neighbouring token as a ticker rather than a word.
_TICKER_CUE = re.compile(
    r"(?:\b(?:nyse|nasdaq|amex|otc|ticker|symbol)\b\s*[:\-]?\s*|\()\s*$", re.IGNORECASE
)

#: Below this length a ticker needs an explicit cue even when upper-case.
MIN_UNCUED_TICKER_LENGTH = 3


@dataclass(frozen=True)
class CompanyRecord:
    """The minimum the resolver needs to know about a company."""

    key: str
    name: str
    ticker: str | None = None
    aliases: tuple[str, ...] = ()
    former_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurfaceForm:
    text: str
    company_key: str
    kind: SurfaceKind
    base_confidence: float


@dataclass(frozen=True)
class EntityMatch:
    """One resolved mention."""

    company_key: str
    surface: str
    kind: SurfaceKind
    confidence: float
    start: int
    end: int
    #: Other companies the same surface could denote. Non-empty means genuinely ambiguous.
    alternatives: tuple[str, ...] = ()

    @property
    def is_ambiguous(self) -> bool:
        return bool(self.alternatives)


@dataclass
class _Candidate:
    start: int
    end: int
    surface: SurfaceForm
    confidence: float
    alternatives: list[str] = field(default_factory=list)


def strip_suffix(name: str) -> str:
    """'Northbridge Memory Corp.' -> 'Northbridge Memory'. Applied repeatedly."""
    previous = None
    current = name.strip()
    while current != previous:
        previous = current
        current = _SUFFIX_RE.sub("", current).strip()
    return current


def suffix_chain(name: str) -> list[str]:
    """Every progressively-shortened form of a name, longest first.

    'Micron Technology, Inc.' -> ['Micron Technology, Inc.', 'Micron Technology', 'Micron']

    All are registered so that longest-match-wins can prefer the most specific form present
    in the text, while a bare 'Micron' still resolves. Each step is less specific than the
    last, so confidence decays along the chain.
    """
    forms: list[str] = []
    current = name.strip()
    while current and current not in forms:
        forms.append(current)
        stripped = _SUFFIX_RE.sub("", current).strip()
        if stripped == current:
            break
        current = stripped
    return forms


def _is_usable_name_surface(text: str) -> bool:
    """Reject surfaces too generic to identify a company."""
    cleaned = _PUNCT.sub("", text).strip()
    if len(cleaned) < 4:
        return False
    tokens = [t.lower() for t in cleaned.split()]
    if not tokens:
        return False
    if len(tokens) == 1 and tokens[0] in _TOO_GENERIC:
        return False
    # A multi-word surface made entirely of generic words identifies nothing.
    return not all(token in _TOO_GENERIC for token in tokens)


class EntityResolver:
    """Resolves company mentions in free text."""

    def __init__(self, companies: list[CompanyRecord]) -> None:
        self._by_surface: dict[str, list[SurfaceForm]] = defaultdict(list)
        self._tickers: dict[str, list[SurfaceForm]] = defaultdict(list)
        self._companies = {c.key: c for c in companies}

        for company in companies:
            for surface in self._surface_forms(company):
                if surface.kind == "ticker":
                    self._tickers[surface.text].append(surface)
                else:
                    self._by_surface[surface.text.lower()].append(surface)

        # Longest alternatives first so the regex alternation prefers the most specific
        # surface at a given position.
        names = sorted(self._by_surface, key=len, reverse=True)
        self._name_re = (
            re.compile(
                r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?!\w)",
                re.IGNORECASE,
            )
            if names
            else None
        )
        self._ticker_re = (
            re.compile(
                r"(?<!\w)(?:"
                + "|".join(re.escape(t) for t in sorted(self._tickers, key=len, reverse=True))
                + r")(?!\w)"
            )
            if self._tickers
            else None
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _surface_forms(company: CompanyRecord) -> list[SurfaceForm]:
        forms: list[SurfaceForm] = []

        def add(text: str, kind: SurfaceKind, confidence: float) -> None:
            text = text.strip()
            if text and _is_usable_name_surface(text):
                forms.append(SurfaceForm(text, company.key, kind, confidence))

        # Register the full legal name and every progressively-shortened form. A shorter
        # form is more collidable ("Apple" vs "Apple Inc."), so confidence decays with each
        # step, and longest-match-wins prefers the most specific form actually written.
        for index, form in enumerate(suffix_chain(company.name)):
            kind: SurfaceKind = "legal_name" if index == 0 else "short_name"
            add(form, kind, max(0.80, 0.98 - 0.06 * index))
        for alias in company.aliases:
            for index, form in enumerate(suffix_chain(alias)):
                add(form, "alias", max(0.78, 0.92 - 0.06 * index))
        for former in company.former_names:
            # A former name may now belong to a different entity entirely, so it is always
            # weaker than any current name.
            for index, form in enumerate(suffix_chain(former)):
                add(form, "former_name", max(0.55, 0.70 - 0.06 * index))

        if company.ticker:
            ticker = company.ticker.strip().upper()
            if ticker:
                forms.append(SurfaceForm(ticker, company.key, "ticker", 0.90))
        return forms

    # ------------------------------------------------------------------
    def resolve(self, text: str, min_confidence: float = 0.5) -> list[EntityMatch]:
        """Return non-overlapping matches, longest and strongest first."""
        candidates: list[_Candidate] = []
        candidates.extend(self._name_candidates(text))
        candidates.extend(self._ticker_candidates(text))

        # Longest span wins; ties broken by confidence. This is what makes
        # "Northbridge Memory Corp" beat the "Northbridge Memory" contained inside it.
        candidates.sort(key=lambda c: (-(c.end - c.start), -c.confidence, c.start))

        taken: list[_Candidate] = []
        for candidate in candidates:
            if candidate.confidence < min_confidence:
                continue
            if any(not (candidate.end <= t.start or candidate.start >= t.end) for t in taken):
                continue
            taken.append(candidate)

        taken.sort(key=lambda c: c.start)
        return [
            EntityMatch(
                company_key=c.surface.company_key,
                surface=text[c.start : c.end],
                kind=c.surface.kind,
                confidence=round(c.confidence, 4),
                start=c.start,
                end=c.end,
                alternatives=tuple(sorted(c.alternatives)),
            )
            for c in taken
        ]

    def _name_candidates(self, text: str) -> list[_Candidate]:
        if self._name_re is None:
            return []
        out: list[_Candidate] = []
        for match in self._name_re.finditer(text):
            forms = self._by_surface.get(match.group(0).lower(), [])
            if not forms:
                continue
            best = max(forms, key=lambda f: f.base_confidence)
            others = [f.company_key for f in forms if f.company_key != best.company_key]
            # Ambiguity lowers confidence rather than being silently resolved.
            confidence = best.base_confidence * (0.6 if others else 1.0)
            out.append(_Candidate(match.start(), match.end(), best, confidence, others))
        return out

    def _ticker_candidates(self, text: str) -> list[_Candidate]:
        """Tickers, gated on casing and cues.

        This gate is the fix for the audit's false positives: prose writes "all", "on" and
        "key" in lower case, so requiring a genuinely upper-case token removes them without
        a hand-maintained blocklist.
        """
        if self._ticker_re is None:
            return []
        out: list[_Candidate] = []
        for match in self._ticker_re.finditer(text):
            token = match.group(0)
            if token != token.upper():
                continue  # "All" is not "ALL"
            forms = self._tickers.get(token, [])
            if not forms:
                continue

            cued = bool(_TICKER_CUE.search(text[max(0, match.start() - 12) : match.start()]))
            if len(token) < MIN_UNCUED_TICKER_LENGTH and not cued:
                continue

            best = forms[0]
            others = [f.company_key for f in forms if f.company_key != best.company_key]
            confidence = 0.95 if cued else 0.72
            if others:
                confidence *= 0.6
            out.append(_Candidate(match.start(), match.end(), best, confidence, others))
        return out

    # ------------------------------------------------------------------
    def resolve_one(self, text: str) -> EntityMatch | None:
        """Highest-confidence unambiguous match, or None.

        Returns None rather than guessing when the best match is ambiguous — an incorrect
        attribution is worse than no attribution.
        """
        matches = [m for m in self.resolve(text) if not m.is_ambiguous]
        return max(matches, key=lambda m: m.confidence) if matches else None
