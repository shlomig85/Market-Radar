"""Provider registry.

Resolves a capability to a configured adapter. Business logic asks for a capability and
never names a vendor, which is what keeps providers replaceable (Master Build Prompt §9).
"""

from __future__ import annotations

from typing import Any

from marketradar.config import Settings, get_settings
from marketradar.domain.enums import ProviderCapability
from marketradar.logging import get_logger
from marketradar.providers.base import ProviderHealth
from marketradar.providers.fixture import (
    FixtureCompanyProvider,
    FixtureFilingsProvider,
    FixtureNewsProvider,
)
from marketradar.providers.sec_edgar import SecCompanyProvider, SecEdgarFilingsProvider
from marketradar.providers.unavailable import UnavailableProvider

log = get_logger(__name__)

_UNCONFIGURED = "No provider configured for this capability in the current environment."


class ProviderRegistry:
    """Capability -> provider, built from settings."""

    def __init__(self, providers: dict[ProviderCapability, Any]) -> None:
        self._providers = providers

    def get(self, capability: ProviderCapability) -> Any:
        provider = self._providers.get(capability)
        if provider is None:
            return UnavailableProvider(capability, _UNCONFIGURED)
        return provider

    @property
    def news(self) -> Any:
        return self.get(ProviderCapability.NEWS_SEARCH)

    @property
    def filings(self) -> Any:
        return self.get(ProviderCapability.FILINGS)

    @property
    def market_data(self) -> Any:
        return self.get(ProviderCapability.MARKET_DATA)

    @property
    def company_data(self) -> Any:
        return self.get(ProviderCapability.COMPANY_DATA)

    def health_report(self) -> list[ProviderHealth]:
        """Health of every capability, including the unavailable ones.

        The unavailable entries matter most: they are what the UI shows so a user can see
        which parts of the analysis had no data behind them.
        """
        report: list[ProviderHealth] = []
        for capability in ProviderCapability:
            report.append(self.get(capability).health())
        return report


def _build_news(settings: Settings) -> Any:
    if settings.news_provider == "fixture":
        return FixtureNewsProvider()
    return UnavailableProvider(
        ProviderCapability.NEWS_SEARCH,
        f"News provider '{settings.news_provider}' is not implemented in this build.",
    )


def _build_filings(settings: Settings) -> Any:
    if settings.filings_provider == "fixture":
        return FixtureFilingsProvider()
    if settings.filings_provider == "sec_edgar":
        return SecEdgarFilingsProvider(
            settings=settings,
            ciks=tuple(settings.sec_ciks),
            max_document_fetches=settings.sec_max_documents,
        )
    return UnavailableProvider(
        ProviderCapability.FILINGS,
        f"Filings provider '{settings.filings_provider}' is not implemented in this build.",
    )


def _build_market_data(settings: Settings) -> Any:
    # No market-data vendor is implemented. This is deliberate and visible: market-derived
    # score components are dropped and the omission is reported (ADR-007).
    return UnavailableProvider(
        ProviderCapability.MARKET_DATA,
        (
            "No market-data provider is configured. Market-derived analysis "
            "(price reaction, valuation, mispricing potential) is not computed, and the "
            "affected score components are reported as unavailable rather than estimated."
        ),
    )


def _build_company(settings: Settings) -> Any:
    if settings.company_provider == "fixture":
        return FixtureCompanyProvider()
    if settings.company_provider == "sec":
        return SecCompanyProvider(settings=settings)
    return UnavailableProvider(
        ProviderCapability.COMPANY_DATA,
        f"Company provider '{settings.company_provider}' is not implemented in this build.",
    )


def build_default_registry(settings: Settings | None = None) -> ProviderRegistry:
    settings = settings or get_settings()
    return ProviderRegistry(
        {
            ProviderCapability.NEWS_SEARCH: _build_news(settings),
            ProviderCapability.FILINGS: _build_filings(settings),
            ProviderCapability.MARKET_DATA: _build_market_data(settings),
            ProviderCapability.COMPANY_DATA: _build_company(settings),
        }
    )
