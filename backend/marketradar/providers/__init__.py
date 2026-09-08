"""Data providers.

Business logic never imports a vendor. It asks the :class:`ProviderRegistry` for a
capability and receives something that satisfies the corresponding protocol — or an
``UNAVAILABLE`` provider, which is a legitimate answer that the rest of the system knows
how to handle.
"""

from marketradar.providers.base import (
    CompanyDataProvider,
    DataProvider,
    FilingsProvider,
    MarketDataProvider,
    NewsSearchProvider,
    ProviderCompany,
    ProviderDocument,
    ProviderHealth,
    ProviderQuery,
    ProviderResult,
    ProviderSource,
)
from marketradar.providers.registry import ProviderRegistry, build_default_registry

__all__ = [
    "CompanyDataProvider",
    "DataProvider",
    "FilingsProvider",
    "MarketDataProvider",
    "NewsSearchProvider",
    "ProviderCompany",
    "ProviderDocument",
    "ProviderHealth",
    "ProviderQuery",
    "ProviderRegistry",
    "ProviderResult",
    "ProviderSource",
    "build_default_registry",
]
