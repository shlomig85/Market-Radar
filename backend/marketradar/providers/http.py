"""Outbound HTTP with an SSRF allowlist.

Every provider that reaches the network goes through :class:`SafeHttpClient`. A URL that is
not ``https`` on an allowlisted host is refused before a connection is opened, so a
malicious or manipulated document cannot steer the system into fetching an internal address.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from marketradar.errors import ProviderError, UnsafeUrlError
from marketradar.logging import get_logger

log = get_logger(__name__)


class RateLimiter:
    """Sliding-window rate limiter.

    SEC's fair-access policy caps clients at 10 requests/second and blocks offenders. The
    clock and sleep function are injected so the limiter can be tested deterministically
    rather than by making tests slow.
    """

    def __init__(
        self,
        max_requests: int,
        per_seconds: float,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a request may proceed. Returns how long it waited."""
        with self._lock:
            now = self._clock()
            cutoff = now - self.per_seconds
            while self._events and self._events[0] <= cutoff:
                self._events.popleft()

            waited = 0.0
            if len(self._events) >= self.max_requests:
                # Wait until the oldest request leaves the window.
                waited = max(0.0, self._events[0] + self.per_seconds - now)
                if waited > 0:
                    self._sleep(waited)
                now = self._clock()
                cutoff = now - self.per_seconds
                while self._events and self._events[0] <= cutoff:
                    self._events.popleft()

            self._events.append(now)
            return waited


class SafeHttpClient:
    """A thin httpx wrapper enforcing scheme + host allowlisting, rate limits and timeouts."""

    def __init__(
        self,
        allowed_hosts: list[str],
        timeout_seconds: float = 20.0,
        headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
        max_response_bytes: int = 25 * 1024 * 1024,
        max_redirects: int = 5,
    ) -> None:
        self._allowed_hosts = {h.lower() for h in allowed_hosts}
        self._rate_limiter = rate_limiter
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers=headers or {},
            # Redirects are followed HERE, not by httpx, so that every hop is re-checked
            # against the allowlist. Delegating to httpx would let a redirect walk the
            # client off the allowlist in a single call, which is the SSRF hole this class
            # exists to close; refusing redirects outright is not an option either, because
            # ordinary feed and article URLs redirect constantly.
            follow_redirects=False,
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

    def _get(self, url: str) -> httpx.Response:
        current = url
        for _ in range(self._max_redirects + 1):
            response = self._get_once(current)
            if not response.is_redirect:
                break
            location = response.headers.get("location", "")
            if not location:
                raise ProviderError("Redirect had no Location header", url=current)
            # Resolved against the current URL so a relative Location works, then checked
            # again: a hop is a new request and gets the same scrutiny as the first one.
            current = str(httpx.URL(current).join(location))
        else:
            raise ProviderError(
                "Too many redirects", url=url, limit=self._max_redirects
            )

        # An oversized response is a denial-of-service vector and a memory hazard; refuse
        # rather than buffer it. Filings are large but bounded.
        if len(response.content) > self._max_response_bytes:
            raise ProviderError(
                "Response exceeded the maximum allowed size",
                url=current,
                size=len(response.content),
                limit=self._max_response_bytes,
            )
        return response

    def _get_once(self, url: str) -> httpx.Response:
        self.check_url(url)
        if self._rate_limiter is not None:
            waited = self._rate_limiter.acquire()
            if waited > 0:
                log.debug("http.rate_limited", url=url, waited_seconds=round(waited, 3))
        try:
            response = self._client.get(url)
            if not response.is_redirect:
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP request failed: {exc}", url=url) from exc
        return response

    def get_json(self, url: str) -> Any:
        response = self._get(url)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Response was not valid JSON: {exc}", url=url) from exc

    def get_text(self, url: str) -> str:
        """Fetch a document body as text. Used for filing documents."""
        return self._get(url).text

    def close(self) -> None:
        self._client.close()
