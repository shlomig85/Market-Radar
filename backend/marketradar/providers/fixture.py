"""Fixture providers backed by the labelled DEMO corpus.

These exist so the pipeline is runnable with no vendor credentials. They are isolated
development adapters: everything they emit carries ``DataMode.DEMO``, and the ingestion
layer refuses to persist a DEMO document under any other mode.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from marketradar.domain.enums import DataMode, ProviderCapability, SourceClass, SourceType
from marketradar.errors import ConfigurationError
from marketradar.providers.base import (
    ProviderCompany,
    ProviderDocument,
    ProviderHealth,
    ProviderQuery,
    ProviderResult,
    ProviderSource,
)

CORPUS_PATH = Path(__file__).resolve().parent.parent / "demo" / "corpus.json"

#: Source types served by the filings capability rather than the news capability. Keeping
#: this split real means the two capabilities are genuinely distinct in tests.
_FILING_SOURCE_TYPES = {
    SourceType.SEC_FILING,
    SourceType.COMPANY_FILING,
    SourceType.REGULATORY_FILING,
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
#: Common words carry no retrieval signal and are dropped before token overlap.
_STOPWORDS = frozenset(
    [
        "a", "an", "the", "of", "for", "and", "or", "to", "in", "on", "is", "are", "be",
        "was", "were", "with", "by", "from", "as", "at", "that", "this", "it", "do", "does",
        "did", "any", "their", "there", "has", "have", "had", "we", "our", "will", "would",
        "could", "should", "than", "then",
    ]
)


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2}


@lru_cache(maxsize=1)
def load_corpus(path: str | None = None) -> dict[str, Any]:
    """Load and cache the demo corpus JSON."""
    target = Path(path) if path else CORPUS_PATH
    if not target.exists():  # pragma: no cover - packaging error
        raise ConfigurationError(f"Demo corpus not found at {target}")
    data: dict[str, Any] = json.loads(target.read_text())
    if data.get("data_mode") != DataMode.DEMO.value:
        raise ConfigurationError("Demo corpus must declare data_mode=DEMO")
    return data


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class _FixtureBase:
    """Shared corpus access and search behaviour."""

    capability: ProviderCapability
    key: str

    def __init__(self, corpus_path: str | None = None) -> None:
        self._corpus = load_corpus(corpus_path)
        self._sources_by_key = {s["key"]: s for s in self._corpus["sources"]}

    # -- source descriptors ---------------------------------------------
    def _source_models(self) -> tuple[ProviderSource, ...]:
        return tuple(
            ProviderSource(
                key=s["key"],
                name=s["name"],
                publisher=s["publisher"],
                source_type=SourceType(s["source_type"]),
                source_class=SourceClass(s["source_class"]),
                base_quality=s["base_quality"],
                homepage_url=s.get("homepage_url"),
                is_synthetic=s.get("is_synthetic", False),
                data_mode=DataMode(s["data_mode"]),
                notes=s.get("notes"),
            )
            for s in self._corpus["sources"]
        )

    def _serves(self, source_key: str) -> bool:
        raise NotImplementedError

    def _documents(self) -> list[dict[str, Any]]:
        return [d for d in self._corpus["documents"] if self._serves(d["source_key"])]

    def _to_document(self, raw: dict[str, Any]) -> ProviderDocument:
        return ProviderDocument(
            external_id=raw["external_id"],
            source_key=raw["source_key"],
            url=raw["url"],
            title=raw["title"],
            body_text=raw["body_text"],
            published_at=_parse(raw["published_at"]),  # type: ignore[arg-type]
            event_at=_parse(raw.get("event_at")),
            origin_ref=raw.get("origin_ref"),
            subject_hints=tuple(raw.get("subject_hints", ())),
            data_mode=DataMode(raw["data_mode"]),
            payload={"corpus_version": self._corpus["corpus_version"]},
        )

    # -- protocol -------------------------------------------------------
    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_key=self.key,
            capability=self.capability,
            available=True,
            mode=DataMode.DEMO,
            detail=(
                f"Synthetic development corpus v{self._corpus['corpus_version']}. "
                "All companies, publishers and statements are invented."
            ),
            checked_at=datetime.now(tz=UTC),
        )

    def sources(self) -> tuple[ProviderSource, ...]:
        return self._source_models()

    def search(self, query: ProviderQuery) -> ProviderResult:
        """Token-overlap retrieval over the corpus.

        Intentionally simple and deterministic: retrieval quality is a provider concern,
        and this adapter's job is to exercise the pipeline, not to simulate a search engine.
        """
        wanted = _tokens(query.text) | {t.lower() for t in query.terms}
        scored: list[tuple[float, dict[str, Any]]] = []
        for raw in self._documents():
            event_at = _parse(raw.get("event_at")) or _parse(raw["published_at"])
            published_at = _parse(raw["published_at"])
            # `since` bounds relevance and is measured on event time: how far back we care
            # about things happening. `until` is the as-of instant and must be measured on
            # PUBLICATION time — a document about an old event is unknowable until published.
            if query.since and event_at and event_at < query.since:
                continue
            if query.until and published_at and published_at > query.until:
                continue
            haystack = _tokens(raw["title"]) | _tokens(raw["body_text"])
            hints = {h.lower() for h in raw.get("subject_hints", [])}
            overlap = len(wanted & haystack)
            if not overlap:
                continue
            score = overlap + (2.0 if query.subject_key and query.subject_key in hints else 0.0)
            scored.append((score, raw))

        scored.sort(key=lambda pair: (-pair[0], pair[1]["published_at"]))
        selected = [raw for _, raw in scored[: query.limit]]
        return ProviderResult(
            provider_key=self.key,
            mode=DataMode.DEMO,
            query=query.text,
            documents=tuple(self._to_document(raw) for raw in selected),
            truncated=len(scored) > query.limit,
            detail="Synthetic development corpus",
        )

    def get_document(self, external_id: str) -> ProviderDocument | None:
        for raw in self._documents():
            if raw["external_id"] == external_id:
                return self._to_document(raw)
        return None

    def get_recent_documents(
        self, limit: int = 100, until: datetime | None = None
    ) -> ProviderResult:
        """Documents this capability serves, newest first, published no later than ``until``.

        ``until`` is the as-of instant. Filtering on *publication* time (not event time) is
        the point: a document about an old event is still unknowable until it is published.
        """
        candidates = self._documents()
        if until is not None:
            candidates = [
                raw for raw in candidates if (_parse(raw["published_at"]) or until) <= until
            ]
        docs = sorted(candidates, key=lambda d: d["published_at"], reverse=True)[:limit]
        return ProviderResult(
            provider_key=self.key,
            mode=DataMode.DEMO,
            query="__recent__",
            documents=tuple(self._to_document(raw) for raw in docs),
            detail="Synthetic development corpus",
        )


class FixtureNewsProvider(_FixtureBase):
    """News, industry, trade, analyst and community documents from the demo corpus."""

    capability = ProviderCapability.NEWS_SEARCH
    key = "fixture_news"

    def _serves(self, source_key: str) -> bool:
        source_type = SourceType(self._sources_by_key[source_key]["source_type"])
        return source_type not in _FILING_SOURCE_TYPES


class FixtureFilingsProvider(_FixtureBase):
    """Regulatory and issuer filing documents from the demo corpus."""

    capability = ProviderCapability.FILINGS
    key = "fixture_filings"

    def _serves(self, source_key: str) -> bool:
        source_type = SourceType(self._sources_by_key[source_key]["source_type"])
        return source_type in _FILING_SOURCE_TYPES


class FixtureCompanyProvider:
    """Company reference data for the fictional demo universe (ADR-006)."""

    capability = ProviderCapability.COMPANY_DATA
    key = "fixture_company"

    def __init__(self, corpus_path: str | None = None) -> None:
        self._corpus = load_corpus(corpus_path)

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_key=self.key,
            capability=self.capability,
            available=True,
            mode=DataMode.DEMO,
            detail="Fictional issuers. No real security is represented.",
            checked_at=datetime.now(tz=UTC),
        )

    def list_companies(self) -> tuple[ProviderCompany, ...]:
        return tuple(
            ProviderCompany(
                key=c["key"],
                name=c["name"],
                ticker=c.get("ticker"),
                exchange=c.get("exchange"),
                industry_key=c.get("industry_key"),
                description=c.get("description"),
                is_fictional=True,
                data_mode=DataMode.DEMO,
            )
            for c in self._corpus["companies"]
        )

    def get_company(self, key: str) -> ProviderCompany | None:
        for company in self.list_companies():
            if company.key == key:
                return company
        return None

    def industries(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._corpus["industries"])

    def relationships(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._corpus["relationships"])
