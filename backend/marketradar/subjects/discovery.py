"""Discovering what the corpus is about, rather than being told.

The Cycle-1 audit's C6 finding: the system could only recognise three subjects — ``memory``,
``ai_infrastructure``, ``semiconductor_equipment`` — because a human had typed their
vocabulary into a lexicon. A system that can only find themes it was taught cannot discover
anything; it monitors. Feed it a hundred articles about grid storage and it sees nothing.

What makes a term a subject
---------------------------
Not frequency. Frequency finds "the company", "third quarter", "forward-looking statements".
Three properties together do the work, and each answers a specific failure:

* **Independent-cluster support.** A term must appear across several *ancestry clusters*,
  not several documents. Ten outlets rewriting one press release are one cluster and one
  confirmation, so a syndicated story cannot mint a subject on its own. This is the whole
  reason the clustering exists and it is doing the load-bearing work here.
* **Specificity.** A term appearing in most documents is boilerplate, not a topic. Document
  frequency above a ceiling disqualifies it outright.
* **Emergence.** An investment theme is something *changing*. A term whose recent rate of
  mention is no higher than its own baseline is a background fact about the world, not an
  emerging theme, and it is ranked accordingly.

Emergence reuses ``bounded_change`` from the trend engine — the same tanh with the same
baseline floor — so "accelerating" means the same thing for a discovered subject as it does
for a measured signal, rather than being a second, divergent notion of the same word.

Why rules and not a model (ADR-008, applied again): this is counting and ranking over a
closed, inspectable procedure. It cannot hallucinate a topic that is not in the corpus, it
is free, and its behaviour is a measurable quantity rather than a vibe. An LLM proposer can
sit behind this same interface once there is a labelled set to measure it against.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from marketradar.trends.engine import bounded_change

DISCOVERY_VERSION = "subject_discovery_v2"

#: A term must be supported by at least this many INDEPENDENT ancestry clusters. Two is a
#: coincidence; three separate origins reporting the same thing is a topic.
MIN_CLUSTERS = 3

#: A term appearing in more than this share of documents is boilerplate, not a subject.
#:
#: Deliberately loose, and a safety net rather than a topic filter. Document frequency
#: already enters the ranking continuously through ``specificity``, and a hard ratio behaves
#: very differently at ten documents than at ten thousand: a genuine topic can easily be in
#: half of a small or narrowly-sourced corpus, so a tight ceiling silently discovers nothing
#: exactly when the corpus is young. Soft ranking does the work; this only removes terms so
#: universal they cannot distinguish anything.
MAX_DOCUMENT_RATIO = 0.75

#: A term appearing in more than this share of ONE PUBLISHER's documents is that
#: publisher's furniture, not a topic. The first live run over real feeds returned "may earn
#: compensation", "california privacy right", "affiliate link policy" and "reproduced
#: distributed transmitted" — cookie notices, affiliate disclosures and copyright footers,
#: which appear in every article a site publishes and in nobody else's.
#:
#: The corpus-wide ratio cannot catch these: one publisher's footer is a small share of a
#: multi-publisher corpus. Per-source repetition is the signal, and it needs no list of
#: known boilerplate phrases — it learns each site's furniture from the site itself.
MAX_SOURCE_RATIO = 0.5

#: Per-source detection needs enough documents from that source to mean anything.
MIN_SOURCE_DOCUMENTS = 4

#: A term in essentially EVERY article a publisher runs is furniture regardless of what
#: other publishers do — no editorial beat is that total, a page footer always is.
UNIVERSAL_SOURCE_RATIO = 0.9

#: ...provided the publisher has a real body of work. Nine of ten is meaningless at n=5.
MIN_UNIVERSAL_DOCUMENTS = 10

#: A term saturating one publisher is furniture only if OTHER publishers barely use it.
#: Above this share elsewhere, it is a subject that publisher happens to cover heavily.
FURNITURE_ELSEWHERE_RATIO = 0.1

#: Below this many documents the ratio above is not a statistic, it is noise: in a corpus of
#: five articles all about one topic, that topic appears in 100% of documents and a ceiling
#: rejects the only real subject present. Independent-cluster support and the stopword list
#: are doing the filtering at that size; the ceiling only switches on once "most documents"
#: means something.
MIN_DOCUMENTS_FOR_RATIO = 20

#: Longest n-gram considered. "high bandwidth memory" is a subject; four-word phrases are
#: almost always sentence fragments.
MAX_NGRAM = 3

#: Terms shorter than this are too collidable to be a subject on their own.
MIN_TERM_CHARS = 4

#: Words that carry no topic on their own. Deliberately *not* a domain vocabulary —
#: nothing here names an industry, a technology or a product, because that is exactly
#: the knowledge this module exists to avoid encoding. It is closed-class English plus
#: the boilerplate that financial documents are made of. Written out rather than built
#: from a .split() so no autoformatter can reflow it into an unreadable single line.
STOPWORDS = frozenset(
    [
    "a", "about", "above", "across", "after", "again",
    "against", "aggregate", "all", "alongside", "already", "also",
    "although", "am", "amid", "among", "an", "analyst",
    "analysts", "and", "announced", "announcement", "annual", "any",
    "approximately", "are", "as", "assumptions", "at", "average",
    "based", "be", "because", "been", "before", "being",
    "below", "between", "billion", "both", "business", "businesses",
    "but", "by", "call", "calls", "can", "cannot",
    "chief", "commentary", "companies", "company", "compared", "corp",
    "corporation", "could", "current", "customer", "customers", "data",
    "decrease", "decreased", "did", "division", "divisions", "do",
    "does", "doing", "down", "during", "each", "earning",
    "earnings", "executive", "expected", "expects", "factors", "few",
    "filing", "filings", "first", "fiscal", "for", "forward",
    "fourth", "from", "further", "growth", "had", "has",
    "have", "having", "he", "her", "here", "hers",
    "him", "his", "holding", "holdings", "how", "however",
    "i", "if", "in", "inc", "include", "included",
    "including", "incorporated", "increase", "increased", "industries", "industry",
    "information", "into", "is", "it", "item", "items",
    "its", "itself", "just", "limited", "llc", "looking",
    "ltd", "management", "market", "markets", "materially", "me",
    "million", "modestly", "month", "months", "more", "most",
    "my", "new", "no", "nor", "not", "note",
    "notes", "of", "off", "officer", "on", "once",
    "only", "operating", "operations", "or", "other", "our",
    "out", "over", "own", "participant", "participants", "per",
    "percent", "percentage", "period", "periods", "plc", "president",
    "price", "prices", "prior", "product", "products", "quarter",
    "quarterly", "release", "report", "reported", "reporting", "reports",
    "respondent", "respondents", "result", "results", "revenue", "revenues",
    "risks", "said", "sales", "same", "say", "says",
    "second", "segment", "segments", "service", "services", "share",
    "shares", "she", "should", "so", "some", "statement",
    "statements", "still", "stock", "such", "survey", "surveyed",
    "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "third", "this", "those",
    "through", "throughout", "to", "today", "too", "total",
    "toward", "towards", "transcript", "transcripts", "uncertainties", "under",
    "until", "up", "versus", "very", "via", "was",
    "we", "week", "weeks", "were", "what", "when",
    "where", "which", "while", "who", "whom", "why",
    "will", "with", "within", "without", "would", "year",
    "years", "yet", "you", "your",
    ]
)

def _predicate_vocabulary() -> frozenset[str]:
    """Words the evidence extractor keys on, which therefore cannot name a subject.

    The first run of discovery over a real corpus returned "capacity", "pricing", "demand",
    "inventory", "declined" — every one of them a *predicate*. Of course it did: a corpus of
    documents about things changing shares the vocabulary of change, so frequency finds it.
    But those are what is HAPPENING, not what it is happening TO, and they are already
    modelled as ``EventType``. A subject has to be the thing: memory, grid storage, AI
    servers.

    Derived from the extraction rules rather than typed out, so it maintains itself: add a
    rule that keys on a new change-word and that word stops being a candidate subject on the
    next run, with nobody remembering to update a list.
    """
    from marketradar.evidence.extractor import (
        FORWARD_LOOKING_PATTERNS,
        RULES,
        STABILITY_PATTERNS,
    )

    words: set[str] = set()
    sources = [rule.pattern.pattern for rule in RULES]
    sources += [pattern.pattern for pattern in STABILITY_PATTERNS]
    sources += [pattern.pattern for pattern in FORWARD_LOOKING_PATTERNS]
    for pattern in sources:
        # Literal alphabetic runs inside the regex. Metacharacters and quantifiers are not
        # word-like and fall out naturally.
        for token in re.findall(r"[a-z]{3,}", pattern.lower()):
            words.add(token)
            # Rules are written against stems ("accelerat", "increas", "declin"), so the
            # inflections that appear in prose are added too.
            for suffix in ("e", "es", "ed", "ing", "s", "y", "ies", "ion", "ions"):
                words.add(token + suffix)
    return frozenset(words)


#: Change-words, from the extraction rules. Computed once at import.
PREDICATE_WORDS = _predicate_vocabulary()

#: A single past-tense token is a verb, not a topic: "declined", "extended", "reported".
#: Only ``-ed`` — ``-ing`` words are frequently nouns ("manufacturing", "shipping").
_PAST_TENSE = re.compile(r"^[a-z]{4,}ed$")


#: Tokens must be word-like: letters, optionally with internal digits or hyphens ("5g",
#: "high-bandwidth"). Pure numbers and symbols are never part of a subject.
_TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]*")
_SENTENCE_SPLIT = re.compile(r"[.!?;:\n]")


@dataclass(frozen=True)
class SubjectObservation:
    """One document, as subject discovery needs to see it."""

    document_id: str
    text: str
    published_at: datetime
    #: Ancestry cluster. Documents repeating one announcement share this, which is what
    #: stops a syndicated story from minting a subject.
    cluster_id: str | None = None
    #: Which publisher this came from. Used to detect that publisher's own boilerplate.
    source_key: str | None = None


@dataclass
class SubjectCandidate:
    """A term the corpus is talking about, with everything that earned it its place."""

    term: str
    key: str
    document_count: int
    cluster_count: int
    #: Clusters mentioning the term inside the observation window.
    recent_clusters: int
    #: Clusters per day over the baseline window, scaled to the observation window length.
    baseline_clusters: float
    first_seen_at: datetime
    last_seen_at: datetime
    #: -100..100, from the same tanh the trend engine uses.
    emergence: float
    #: 0..1. How much of the corpus this term does NOT appear in; boilerplate scores low.
    specificity: float
    example_documents: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """Rank order: emerging, specific, and independently corroborated.

        Emergence is the product's actual question ("what is changing?"), so it leads.
        Specificity keeps boilerplate out. Corroboration saturates rather than scaling
        linearly, so a term in forty clusters does not bury one in six purely on volume —
        past a handful of independent origins, more of them adds little to the case.
        """
        corroboration = min(1.0, self.cluster_count / (MIN_CLUSTERS * 3))
        return round(
            max(0.0, self.emergence) * 0.6
            + self.specificity * 100 * 0.25
            + corroboration * 100 * 0.15,
            4,
        )


def normalise_term(term: str) -> str:
    """Fold a term to its comparison form.

    Only a plural fold, deliberately: a real stemmer would collapse "memory"/"memories" and
    also "capacity"/"capacitor", and inventing a subject by over-stemming is worse than
    carrying two spellings of one.
    """
    words = []
    for word in term.lower().split():
        if len(word) > 3 and word.endswith("ies"):
            words.append(word[:-3] + "y")
        elif len(word) > 3 and word.endswith("ses"):
            words.append(word[:-2])
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            words.append(word[:-1])
        else:
            words.append(word)
    return " ".join(words)


def subject_key_for(term: str) -> str:
    """Stable identifier for a subject: ``high bandwidth memory`` -> ``high_bandwidth_memory``."""
    return re.sub(r"[^a-z0-9]+", "_", normalise_term(term)).strip("_")


def candidate_terms(text: str) -> set[str]:
    """Every n-gram in a document that could plausibly name a subject.

    N-grams are built per sentence so a phrase never spans a full stop, and a term may
    neither begin nor end with a stopword — "of memory demand" and "memory demand is" are
    fragments of the same subject, and only "memory demand" is the subject.
    """
    found: set[str] = set()
    for sentence in _SENTENCE_SPLIT.split(text.lower()):
        tokens = _TOKEN_RE.findall(sentence)
        for size in range(1, MAX_NGRAM + 1):
            for index in range(len(tokens) - size + 1):
                gram = tokens[index : index + size]
                # A term may neither begin nor end with a stopword or a change-word.
                # Stopwords give fragments ("of memory demand"); change-words give the
                # predicate rather than the subject — and "memory pricing" is not a subject,
                # it is the subject "memory" measured by the pricing signal template, which
                # the signal layer already generates.
                if gram[0] in STOPWORDS or gram[-1] in STOPWORDS:
                    continue
                if gram[0] in PREDICATE_WORDS or gram[-1] in PREDICATE_WORDS:
                    continue
                if any(len(token) < 2 for token in gram):
                    continue
                term = " ".join(gram)
                if len(term) < MIN_TERM_CHARS:
                    continue
                # A single word that is change-vocabulary or a past participle describes an
                # event, not a subject. Multi-word phrases are allowed to contain one
                # ("memory pricing" is a real topic) provided they are not made only of them.
                if len(gram) == 1 and (
                    gram[0] in PREDICATE_WORDS or _PAST_TENSE.match(gram[0])
                ):
                    continue
                if all(
                    token in PREDICATE_WORDS or token in STOPWORDS for token in gram
                ):
                    continue
                # A single token that is a bare number-like or all-stopword phrase is out;
                # multi-word terms are allowed an interior stopword ("cost of capital").
                if all(token in STOPWORDS for token in gram):
                    continue
                found.add(normalise_term(term))
    return found


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Word-boundary containment, so "memory" is inside "memory demand" but not "said"."""
    return bool(re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack))


def _is_subsumed(term: str, clusters: int, kept: dict[str, int]) -> bool:
    """True when a term already kept covers this one.

    Two directions, and the interesting one is the second:

    Candidates arrive longest-first, and a term is dropped when a kept phrase contains it
    with **equal** support. Equal support means the shorter term never occurs outside the
    longer one — "grid" and "storage" appearing in exactly the clusters that say "grid
    storage" is the evidence that the phrase, not either word, is the unit. This is ordinary
    collocation extraction, and without it one topic is reported three times and its
    evidence splits three ways through every stage downstream.

    Equality is the test rather than mere containment, because a sub-phrase with strictly
    *more* support is a genuinely broader topic: "memory" appears in filings that never
    mention "high-bandwidth memory", so both are real subjects and both are kept.
    """
    for other, other_clusters in kept.items():
        if other == term:
            continue
        if _contains_phrase(other, term) and other_clusters == clusters:
            return True
    return False


#: A sentence repeated verbatim in at least this many documents is not editorial content.
#: Independent journalists do not produce identical sentences; navigation, promo blocks,
#: affiliate disclosures and legal footers are identical by construction.
BOILERPLATE_DOCUMENTS = 4

#: ...and it must be long enough that repetition means something. "Shares fell." recurs
#: legitimately; a twelve-word sentence recurring across four documents does not.
BOILERPLATE_MIN_WORDS = 5


def repeated_sentences(
    observations: list[SubjectObservation],
    min_documents: int = BOILERPLATE_DOCUMENTS,
) -> frozenset[str]:
    """Sentences appearing verbatim across several documents: the boilerplate.

    This replaces proportion-based furniture detection, which could not work. The tell in
    the live data was that thirty "subjects" shared *identical* statistics — same cluster
    count, same emergence, same salience — because they are one repeated block of text, not
    thirty topics. But that block sat in only ~13% of its publisher's documents (extraction
    succeeds on some page templates and not others), so no saturation threshold could reach
    it without also rejecting real topics.

    Verbatim repetition is the signal that does not depend on how much a publisher wrote or
    on how many publishers there are. Two independently written articles do not share a
    sentence; a footer is the same sentence every time. Syndicated copy does repeat, but
    ancestry clustering already collapses it to one confirmation, so removing it here costs
    nothing that was going to count.
    """
    counts: defaultdict[str, set[str]] = defaultdict(set)
    for observation in observations:
        for sentence in _boilerplate_candidates(observation.text):
            counts[sentence].add(observation.document_id)
    return frozenset(
        sentence for sentence, documents in counts.items() if len(documents) >= min_documents
    )


def _boilerplate_candidates(text: str) -> set[str]:
    """Normalised sentences from a document, long enough for repetition to be meaningful."""
    found: set[str] = set()
    for raw in _SENTENCE_SPLIT.split(text):
        sentence = " ".join(raw.lower().split())
        if len(sentence.split()) >= BOILERPLATE_MIN_WORDS:
            found.add(sentence)
    return found


def strip_boilerplate(text: str, boilerplate: frozenset[str]) -> str:
    """Remove known boilerplate sentences, keeping everything else intact."""
    if not boilerplate:
        return text
    kept = []
    for raw in _SENTENCE_SPLIT.split(text):
        if " ".join(raw.lower().split()) not in boilerplate:
            kept.append(raw)
    return ". ".join(kept)


def _is_publisher_furniture(
    by_source: dict[str, set[str]], source_totals: dict[str, int]
) -> bool:
    """True when a term saturates ONE publisher and is largely absent from the others.

    Furniture is a *contrast between* sources, not saturation alone. A site's legal footer
    is on every page it publishes and on nobody else's; a site's **beat** — a trade journal
    that only covers semiconductors — also saturates that source, but other publishers use
    the term too. Testing saturation alone cannot tell them apart, and rejected "grid
    storage" from a corpus that was entirely about grid storage.

    So a term is furniture only when another publisher had a fair chance to use it and
    essentially did not. With one source in the corpus there is no contrast to measure, and
    nothing is called furniture.
    """
    for source, seen in by_source.items():
        total = source_totals.get(source, 0)
        if total < MIN_SOURCE_DOCUMENTS:
            continue
        share = len(seen) / total
        if share <= MAX_SOURCE_RATIO:
            continue

        # Near-total saturation of a source with a real body of work needs no contrast to
        # judge. A publisher does not write about one topic in essentially EVERY article it
        # publishes; its footer appears in every one by construction. This closes the
        # escape hatch below, which otherwise keeps boilerplate whenever one publisher
        # dominates the corpus — the case where most other feeds' articles failed to
        # extract and were dropped, leaving nothing to contrast against.
        if share >= UNIVERSAL_SOURCE_RATIO and total >= MIN_UNIVERSAL_DOCUMENTS:
            return True

        elsewhere_total = sum(
            count for other, count in source_totals.items() if other != source
        )
        if elsewhere_total < MIN_SOURCE_DOCUMENTS:
            continue  # no contrast available; cannot tell furniture from a beat
        elsewhere_seen = sum(
            len(documents) for other, documents in by_source.items() if other != source
        )
        if elsewhere_seen / elsewhere_total <= FURNITURE_ELSEWHERE_RATIO:
            return True
    return False


def discover_subjects(
    observations: list[SubjectObservation],
    as_of: datetime,
    observation_window_days: int = 30,
    baseline_window_days: int = 90,
    limit: int = 40,
    min_clusters: int = MIN_CLUSTERS,
    excluded_terms: frozenset[str] | None = None,
) -> list[SubjectCandidate]:
    """Rank the subjects a corpus is actually about.

    ``as_of`` bounds the read: a document published after it is not considered at all, so a
    historical replay discovers what was discoverable then rather than what is obvious now.
    """
    visible = [o for o in observations if o.published_at <= as_of]
    if not visible:
        return []

    total_documents = len(visible)
    window_start = as_of - timedelta(days=observation_window_days)
    baseline_start = as_of - timedelta(days=observation_window_days + baseline_window_days)

    documents: defaultdict[str, set[str]] = defaultdict(set)
    # term -> source -> documents from that source containing it.
    per_source: defaultdict[str, defaultdict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    source_totals: defaultdict[str, int] = defaultdict(int)
    clusters: defaultdict[str, set[str]] = defaultdict(set)
    recent: defaultdict[str, set[str]] = defaultdict(set)
    baseline: defaultdict[str, set[str]] = defaultdict(set)
    first_seen: dict[str, datetime] = {}
    last_seen: dict[str, datetime] = {}
    examples: defaultdict[str, list[str]] = defaultdict(list)

    # Boilerplate is identified across the whole corpus first, then removed from every
    # document before a single candidate term is generated. Filtering terms afterwards was
    # the mistake: by then the footer is indistinguishable from a topic except by
    # proportion, and proportion does not separate them.
    boilerplate = repeated_sentences(visible)

    for observation in visible:
        # A document with no cluster is its own origin rather than being lumped with every
        # other unclustered document, which would understate independence to zero.
        cluster = observation.cluster_id or f"doc:{observation.document_id}"
        source = observation.source_key or "unknown"
        source_totals[source] += 1
        for term in candidate_terms(strip_boilerplate(observation.text, boilerplate)):
            documents[term].add(observation.document_id)
            per_source[term][source].add(observation.document_id)
            clusters[term].add(cluster)
            if observation.published_at >= window_start:
                recent[term].add(cluster)
            elif observation.published_at >= baseline_start:
                baseline[term].add(cluster)
            if term not in first_seen or observation.published_at < first_seen[term]:
                first_seen[term] = observation.published_at
            if term not in last_seen or observation.published_at > last_seen[term]:
                last_seen[term] = observation.published_at
            if len(examples[term]) < 3:
                examples[term].append(observation.document_id)

    excluded = excluded_terms or frozenset()
    candidates: list[SubjectCandidate] = []
    for term, cluster_ids in clusters.items():
        if len(cluster_ids) < min_clusters:
            continue
        # A company name is an ENTITY, not a subject: "Northbridge Memory Corp" is who the
        # news is about, and entity resolution already maps it to an issuer. Admitting it
        # here would double-model it and let one company's coverage look like a theme.
        if term in excluded:
            continue
        document_ratio = len(documents[term]) / total_documents
        if total_documents >= MIN_DOCUMENTS_FOR_RATIO and document_ratio > MAX_DOCUMENT_RATIO:
            continue
        if _is_publisher_furniture(per_source[term], source_totals):
            continue

        # Baseline is rescaled to the observation window's length so the two are comparable
        # rates rather than two raw counts over different spans.
        scale = observation_window_days / max(baseline_window_days, 1)
        baseline_rate = len(baseline[term]) * scale
        candidates.append(
            SubjectCandidate(
                term=term,
                key=subject_key_for(term),
                document_count=len(documents[term]),
                cluster_count=len(cluster_ids),
                recent_clusters=len(recent[term]),
                baseline_clusters=round(baseline_rate, 4),
                first_seen_at=first_seen[term],
                last_seen_at=last_seen[term],
                emergence=bounded_change(len(recent[term]), baseline_rate),
                specificity=round(1.0 - document_ratio, 4),
                example_documents=examples[term],
            )
        )

    # Longest phrase first on a tie, so a multi-word topic is considered before the single
    # words inside it and those words are then subsumed by it.
    candidates.sort(key=lambda c: (-c.score, -len(c.term)))

    # Drop terms already covered by a longer, better-scoring phrase.
    kept: list[SubjectCandidate] = []
    seen_terms: dict[str, int] = {}
    for candidate in candidates:
        if _is_subsumed(candidate.term, candidate.cluster_count, seen_terms):
            continue
        seen_terms[candidate.term] = candidate.cluster_count
        kept.append(candidate)
        if len(kept) >= limit:
            break
    return kept


__all__ = [
    "DISCOVERY_VERSION",
    "MAX_DOCUMENT_RATIO",
    "BOILERPLATE_DOCUMENTS",
    "MIN_CLUSTERS",
    "MAX_SOURCE_RATIO",
    "PREDICATE_WORDS",
    "MIN_DOCUMENTS_FOR_RATIO",
    "STOPWORDS",
    "SubjectCandidate",
    "SubjectObservation",
    "candidate_terms",
    "discover_subjects",
    "repeated_sentences",
    "strip_boilerplate",
    "normalise_term",
    "subject_key_for",
]
