"""Persisting discovered subjects, and building the vocabulary the extractor reads with.

Discovery finds what the corpus is about; this stores it so a subject keeps a stable key
and a real first-seen date across runs. Without persistence, "when did this become
visible?" — the product's north-star question — has no answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.config import get_settings
from marketradar.domain.enums import DataMode
from marketradar.domain.models import (
    Company,
    EvidenceItem,
    Source,
    SourceDocument,
    Subject,
)
from marketradar.entities.resolver import suffix_chain
from marketradar.evidence.extractor import SUBJECT_LEXICON
from marketradar.logging import get_logger
from marketradar.subjects.discovery import (
    DISCOVERY_VERSION,
    MAX_NGRAM,
    SubjectObservation,
    discover_subjects,
    normalise_term,
)

log = get_logger(__name__)


@dataclass
class SubjectReport:
    """What discovery found this run."""

    considered: int = 0
    created: int = 0
    updated: int = 0
    #: Discovered subjects withdrawn because they no longer qualify.
    retracted: int = 0
    top_terms: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def seed_lexicon_subjects(session: Session, as_of: datetime) -> int:
    """Ensure the built-in lexicon subjects exist, flagged as declared rather than found.

    They are kept so the development corpus and its signal definitions keep working, and
    they carry ``is_discovered = False`` so the system never claims to have discovered a
    topic that was typed into it.
    """
    existing = {key for (key,) in session.execute(select(Subject.key)).all()}
    created = 0
    for key, vocabulary in SUBJECT_LEXICON.items():
        if key in existing:
            continue
        session.add(
            Subject(
                key=key,
                term=vocabulary[0] if vocabulary else key,
                label=key.replace("_", " ").title(),
                first_seen_at=as_of,
                last_seen_at=as_of,
                discovered_at=as_of,
                is_discovered=False,
                discovery_version="lexicon",
                data_mode=DataMode.DEMO,
            )
        )
        created += 1
    session.flush()
    return created


def company_ngrams(session: Session) -> frozenset[str]:
    """Every phrase inside a known company's name, as discovery would tokenise it.

    Excluding only the whole name and its suffix chain is not enough: candidates are
    n-grams, so "Northbridge Memory Corp" still leaks in as "memory corp", "northbridge"
    and "semiconductor memory". Each of those is part of an issuer's name, and an issuer is
    an entity that resolution already handles — admitting it as a subject double-models it
    and lets a single company's news coverage read as a theme.
    """
    phrases: set[str] = set()
    for company in session.scalars(select(Company)).all():
        for surface in suffix_chain(company.name):
            tokens = normalise_term(surface).split()
            for size in range(1, min(len(tokens), MAX_NGRAM) + 1):
                for index in range(len(tokens) - size + 1):
                    phrases.add(" ".join(tokens[index : index + size]))
    return frozenset(phrases)


def publisher_ngrams(session: Session) -> frozenset[str]:
    """Every phrase inside a subscribed publisher's own name.

    A live run returned "cnbc", "cond nast" and "technica addendum" as discovered subjects.
    A publisher is not a topic — it is where topics are reported from — and its name recurs
    in its own pages by construction. Companies were already excluded this way; publishers
    were not, purely because I did not think of them.

    Derived from the Source rows, so subscribing to a new feed excludes that publisher's
    name automatically.
    """
    phrases: set[str] = set()
    for source in session.scalars(select(Source)).all():
        for label in (source.name, source.publisher, source.key.replace("-", " ")):
            tokens = normalise_term(label).split()
            for size in range(1, min(len(tokens), MAX_NGRAM) + 1):
                for index in range(len(tokens) - size + 1):
                    phrase = " ".join(tokens[index : index + size])
                    if len(phrase) >= 3:
                        phrases.add(phrase)
    return frozenset(phrases)


def refresh_subjects(
    session: Session,
    as_of: datetime,
    observation_window_days: int = 30,
    baseline_window_days: int = 90,
    limit: int = 40,
) -> SubjectReport:
    """Discover subjects from the stored corpus and persist them."""
    report = SubjectReport()
    report.created += seed_lexicon_subjects(session, as_of)

    documents = session.scalars(select(SourceDocument)).all()
    report.considered = len(documents)
    if not documents:
        return report

    # Company names are entities, resolved elsewhere; they must not also become subjects.
    # Every surface form the resolver knows is excluded, including the shortened ones, so
    # "Northbridge" is refused as well as "Northbridge Memory Corp".
    settings = get_settings()
    # Rules cover categories; this covers the specifics only the operator knows.
    operator_terms = frozenset(
        normalise_term(term) for term in settings.subject_exclusions if term.strip()
    )
    excluded = company_ngrams(session) | publisher_ngrams(session) | operator_terms

    candidates = discover_subjects(
        [
            SubjectObservation(
                document_id=document.id,
                # The title carries the topic in the most compressed form available and is
                # weighted by being counted alongside the body rather than instead of it.
                text=f"{document.title}. {document.body_text}",
                published_at=document.published_at,
                cluster_id=document.cluster_id,
                source_key=document.source_id,
            )
            for document in documents
        ],
        as_of=as_of,
        observation_window_days=observation_window_days,
        baseline_window_days=baseline_window_days,
        limit=limit,
        excluded_terms=excluded,
    )

    # A term already covered by a declared subject's vocabulary must not become a second
    # subject. "artificial intelligence" is both a discovered n-gram and part of the
    # ai_infrastructure lexicon; admitting both splits one topic's evidence across two
    # subjects, and which one a sentence lands in comes down to a tie-break on key names.
    declared_vocabulary = {
        normalise_term(term)
        for subject in session.scalars(select(Subject)).all()
        if not subject.is_discovered
        for term in SUBJECT_LEXICON.get(subject.key, ())
    }

    existing = {subject.key: subject for subject in session.scalars(select(Subject)).all()}
    for candidate in candidates:
        if candidate.key not in existing and candidate.term in declared_vocabulary:
            report.notes.append(
                f"'{candidate.term}' is already covered by a declared subject; not duplicated"
            )
            continue
        row = existing.get(candidate.key)
        if row is None:
            session.add(
                Subject(
                    key=candidate.key,
                    term=candidate.term,
                    label=candidate.term.title(),
                    document_count=candidate.document_count,
                    cluster_count=candidate.cluster_count,
                    emergence=candidate.emergence,
                    specificity=candidate.specificity,
                    salience=candidate.score,
                    first_seen_at=candidate.first_seen_at,
                    last_seen_at=candidate.last_seen_at,
                    discovered_at=as_of,
                    is_discovered=True,
                    discovery_version=DISCOVERY_VERSION,
                    data_mode=DataMode.LIVE,
                )
            )
            report.created += 1
            continue

        row.document_count = candidate.document_count
        row.cluster_count = candidate.cluster_count
        row.emergence = candidate.emergence
        row.specificity = candidate.specificity
        row.salience = candidate.score
        # first_seen_at only ever moves EARLIER. A subject's age is measured from when it
        # first appeared in the corpus, and letting a later run push it forward would erase
        # exactly the lead time this product exists to measure.
        row.first_seen_at = min(row.first_seen_at, candidate.first_seen_at)
        row.last_seen_at = max(row.last_seen_at, candidate.last_seen_at)
        report.updated += 1

    # --- retract subjects that no longer qualify ---------------------------
    # Discovery output is derived data and must be withdrawable, exactly like evidence
    # (ADR-017). Without this, a subject discovered once is a subject forever: a run that
    # produced website furniture left "cookie preference" and "reprint permission" sitting
    # in the table after the filter that would have rejected them was already in place.
    #
    # A subject still referenced by evidence is kept whatever discovery says this run —
    # deleting it would strand rows that point at it.
    in_use = subjects_with_evidence(session)
    still_found = {candidate.key for candidate in candidates}
    for subject in session.scalars(select(Subject).where(Subject.is_discovered.is_(True))).all():
        if subject.key in still_found or subject.key in in_use:
            continue
        session.delete(subject)
        report.retracted += 1

    session.flush()
    report.top_terms = [candidate.term for candidate in candidates[:10]]
    log.info(
        "subjects.refreshed",
        considered=report.considered,
        created=report.created,
        updated=report.updated,
        top=report.top_terms[:5],
    )
    return report


def subject_vocabulary(session: Session) -> dict[str, tuple[str, ...]]:
    """The vocabulary the evidence extractor should read with.

    Discovered subjects contribute their term; the built-in lexicon subjects contribute
    their typed-in vocabulary, so the development corpus keeps working while real corpora
    are read with terms that came out of the corpus itself.
    """
    vocabulary: dict[str, tuple[str, ...]] = {}
    for subject in session.scalars(select(Subject)).all():
        if subject.is_discovered:
            vocabulary[subject.key] = (subject.term,)
        else:
            vocabulary[subject.key] = SUBJECT_LEXICON.get(subject.key, (subject.term,))
    return vocabulary or dict(SUBJECT_LEXICON)


def active_subject_keys(session: Session, minimum_clusters: int = 0) -> list[str]:
    """Subjects worth measuring, most salient first."""
    rows = session.scalars(
        select(Subject).where(Subject.cluster_count >= minimum_clusters)
    ).all()
    return [row.key for row in sorted(rows, key=lambda s: -s.salience)]


def subjects_with_evidence(session: Session) -> set[str]:
    """Subject keys that actually have evidence attached.

    Instantiating signals for a subject with no evidence produces a wall of empty trends
    that dilute every aggregate and tell a reader nothing.
    """
    return {
        key
        for (key,) in session.execute(
            select(EvidenceItem.subject_key).where(EvidenceItem.subject_key.is_not(None))
        ).all()
        if key
    }


__all__ = [
    "SubjectReport",
    "company_ngrams",
    "publisher_ngrams",
    "active_subject_keys",
    "refresh_subjects",
    "seed_lexicon_subjects",
    "subject_vocabulary",
    "subjects_with_evidence",
]
