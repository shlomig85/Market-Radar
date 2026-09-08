"""Explicitly unavailable providers.

When a capability has no configured, reachable vendor the registry returns one of these.
It answers ``UNAVAILABLE`` truthfully; it never returns an empty result that a caller could
mistake for "we looked and there is nothing".
"""

from __future__ import annotations

from datetime import UTC, datetime

from marketradar.domain.enums import DataMode, ProviderCapability
from marketradar.providers.base import (
    MarketSnapshot,
    ProviderCompany,
    ProviderDocument,
    ProviderHealth,
    ProviderQuery,
    ProviderResult,
    ProviderSource,
)


class UnavailableProvider:
    """Satisfies every provider protocol while reporting that it has no data."""

    def __init__(self, capability: ProviderCapability, reason: str, key: str | None = None):
        self.capability = capability
        self.key = key or f"unavailable_{capability.value.lower()}"
        self._reason = reason

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_key=self.key,
            capability=self.capability,
            available=False,
            mode=DataMode.UNAVAILABLE,
            detail=self._reason,
            checked_at=datetime.now(tz=UTC),
        )

    # --- search-shaped capabilities -------------------------------------
    def search(self, query: ProviderQuery) -> ProviderResult:
        return ProviderResult(
            provider_key=self.key,
            mode=DataMode.UNAVAILABLE,
            query=query.text,
            documents=(),
            detail=self._reason,
        )

    def get_document(self, external_id: str) -> ProviderDocument | None:
        return None

    def sources(self) -> tuple[ProviderSource, ...]:
        return ()

    # --- market data ----------------------------------------------------
    def get_snapshot(self, ticker: str) -> MarketSnapshot | None:
        """Return a snapshot whose mode is UNAVAILABLE.

        Deliberately not ``None``: the caller must be able to record *why* a market-derived
        score component was dropped, and a typed answer carries that reason.
        """
        return MarketSnapshot(ticker=ticker, mode=DataMode.UNAVAILABLE, detail=self._reason)

    # --- company data ---------------------------------------------------
    def list_companies(self) -> tuple[ProviderCompany, ...]:
        return ()

    def get_company(self, key: str) -> ProviderCompany | None:
        return None
