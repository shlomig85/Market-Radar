"""Provider interfaces and transport-neutral DTOs.

Two rules govern this module:

1. A provider returns **typed DTOs**, never ORM objects and never raw vendor JSON. The
   ingestion layer is what decides how a document becomes a row.
2. A provider that cannot answer says so via :class:`ProviderHealth` with
   ``DataMode.UNAVAILABLE``. It never returns an approximation, a placeholder or a
   fabricated value.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from marketradar.domain.enums import (
    DataMode,
    ProviderCapability,
    SourceClass,
    SourceType,
)


class ProviderSource(BaseModel):
    """Descriptor for a publisher the provider can emit documents from."""

    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    publisher: str
    source_type: SourceType
    source_class: SourceClass
    base_quality: int = Field(ge=0, le=100)
    homepage_url: str | None = None
    is_synthetic: bool = False
    data_mode: DataMode
    notes: str | None = None


class ProviderDocument(BaseModel):
    """A document as the provider sees it, before normalisation.

    ``event_at`` is optional on purpose: many providers only know when something was
    *published*. Ingestion then records an inferred event time and flags it, rather than
    silently treating publication as occurrence (ADR-013).
    """

    model_config = ConfigDict(frozen=True)

    external_id: str
    source_key: str
    url: str
    title: str
    body_text: str
    published_at: datetime
    event_at: datetime | None = None
    author: str | None = None
    language: str = "en"
    #: Declared upstream announcement this document is reporting on, when known. This is
    #: the strongest ancestry evidence available and is used before text similarity.
    origin_ref: str | None = None
    subject_hints: tuple[str, ...] = ()
    data_mode: DataMode
    payload: dict[str, Any] = Field(default_factory=dict)


class ProviderCompany(BaseModel):
    """A company as the provider sees it."""

    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    ticker: str | None = None
    exchange: str | None = None
    cik: str | None = None
    country: str | None = None
    industry_key: str | None = None
    description: str | None = None
    is_fictional: bool = False
    data_mode: DataMode


class ProviderQuery(BaseModel):
    """A search request. Providers translate this into their own query language."""

    model_config = ConfigDict(frozen=True)

    text: str
    terms: tuple[str, ...] = ()
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 25
    subject_key: str | None = None


class ProviderResult(BaseModel):
    """The outcome of a provider call, always carrying its own provenance mode."""

    model_config = ConfigDict(frozen=True)

    provider_key: str
    mode: DataMode
    query: str
    documents: tuple[ProviderDocument, ...] = ()
    truncated: bool = False
    latency_ms: int | None = None
    detail: str | None = None

    @property
    def count(self) -> int:
        return len(self.documents)


class ProviderHealth(BaseModel):
    """Whether a provider can currently answer, and in what mode."""

    model_config = ConfigDict(frozen=True)

    provider_key: str
    capability: ProviderCapability
    available: bool
    mode: DataMode
    detail: str
    checked_at: datetime
    #: True when the adapter targets a real external service whose live path has not been
    #: exercised in this environment. Surfaced verbatim rather than glossed over.
    live_path_verified: bool = True


@runtime_checkable
class DataProvider(Protocol):
    """Common surface for every provider."""

    key: str
    capability: ProviderCapability

    def health(self) -> ProviderHealth: ...


@runtime_checkable
class NewsSearchProvider(DataProvider, Protocol):
    """News / general web search over documents."""

    def search(self, query: ProviderQuery) -> ProviderResult: ...

    def get_document(self, external_id: str) -> ProviderDocument | None: ...

    def sources(self) -> tuple[ProviderSource, ...]: ...


@runtime_checkable
class FilingsProvider(DataProvider, Protocol):
    """Regulatory filings (SEC EDGAR and equivalents)."""

    def search(self, query: ProviderQuery) -> ProviderResult: ...

    def get_document(self, external_id: str) -> ProviderDocument | None: ...

    def sources(self) -> tuple[ProviderSource, ...]: ...


@runtime_checkable
class MarketDataProvider(DataProvider, Protocol):
    """Prices, volumes and derived market statistics."""

    def get_snapshot(self, ticker: str) -> MarketSnapshot | None: ...


@runtime_checkable
class CompanyDataProvider(DataProvider, Protocol):
    """Company reference data."""

    def list_companies(self) -> tuple[ProviderCompany, ...]: ...

    def get_company(self, key: str) -> ProviderCompany | None: ...


class MarketSnapshot(BaseModel):
    """Market state for one security.

    Every field is optional and the mode is explicit: a snapshot with no data is a valid,
    honest answer, and consumers must handle it rather than defaulting to zero.
    """

    model_config = ConfigDict(frozen=True)

    ticker: str
    mode: DataMode
    as_of: datetime | None = None
    price: float | None = None
    change_1m_pct: float | None = None
    change_3m_pct: float | None = None
    relative_strength_vs_sector: float | None = None
    volume_ratio: float | None = None
    detail: str | None = None
