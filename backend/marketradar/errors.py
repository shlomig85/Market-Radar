"""Domain error hierarchy.

Nothing in the system swallows an exception silently. Errors carry a stable ``code`` so the
API layer can map them to HTTP responses in exactly one place.
"""

from __future__ import annotations

from typing import Any


class MarketRadarError(Exception):
    """Base class for all application errors."""

    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}


class NotFoundError(MarketRadarError):
    code = "not_found"
    http_status = 404


class ValidationError(MarketRadarError):
    code = "validation_error"
    http_status = 422


class ConfigurationError(MarketRadarError):
    code = "configuration_error"
    http_status = 500


class ProviderError(MarketRadarError):
    """A data provider failed. Must never crash the pipeline: callers degrade instead."""

    code = "provider_error"
    http_status = 502


class ProviderUnavailableError(ProviderError):
    """The provider is not configured or not reachable. Expected, not exceptional."""

    code = "provider_unavailable"
    http_status = 503


class UnsafeUrlError(ProviderError):
    """An outbound URL failed the SSRF allowlist check."""

    code = "unsafe_url"
    http_status = 400


class BudgetExceededError(MarketRadarError):
    """An agent hit its configured budget and was stopped."""

    code = "budget_exceeded"
    http_status = 409
