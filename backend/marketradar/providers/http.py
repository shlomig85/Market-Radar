"""Outbound HTTP with an SSRF allowlist.

Every provider that reaches the network goes through :class:`SafeHttpClient`. A URL that is
not ``https`` on an allowlisted host is refused before a connection is opened, so a
malicious or manipulated document cannot steer the system into fetching an internal address.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from marketradar.errors import ProviderError, UnsafeUrlError
from marketradar.logging import get_logger

log = get_logger(__name__)


class SafeHttpClient:
    """A thin httpx wrapper enforcing scheme + host allowlisting and timeouts."""

    def __init__(
        self,
        allowed_hosts: list[str],
        timeout_seconds: float = 20.0,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._allowed_hosts = {h.lower() for h in allowed_hosts}
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers=headers or {},
            follow_redirects=False,  # a redirect could leave the allowlist
            transport=transport,
        )

    def check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise UnsafeUrlError("Only https URLs may be fetched", url=url)
        host = (parsed.hostname or "").lower()
        if host not in self._allowed_hosts:
            raise UnsafeUrlError(
                "Host is not in the outbound allowlist",
                url=url,
                host=host,
                allowed=sorted(self._allowed_hosts),
            )

    def get_json(self, url: str) -> Any:
        self.check_url(url)
        try:
            response = self._client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP request failed: {exc}", url=url) from exc

    def close(self) -> None:
        self._client.close()
