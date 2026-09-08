"""SEC EDGAR filings provider.

Real adapter against ``data.sec.gov``. It is the reference implementation showing that the
provider interface fits a genuine external API rather than only a fixture.

**Verification status (ADR-010).** The parsing layer is unit-tested against recorded-shape
payloads. The live HTTP path has *not* been exercised in the current development
environment, where outbound access to ``sec.gov`` is blocked at the network boundary
(``403`` at the proxy). ``health()`` therefore reports ``UNAVAILABLE`` here — the adapter
does not claim to be working when it cannot reach its API. This limitation is stated in the
README and in the cycle report rather than hidden.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from marketradar.config import Settings, get_settings
from marketradar.domain.enums import DataMode, ProviderCapability, SourceClass, SourceType
from marketradar.errors import ProviderError
from marketradar.logging import get_logger
from marketradar.providers.base import (
    ProviderDocument,
    ProviderHealth,
    ProviderQuery,
    ProviderResult,
    ProviderSource,
)
from marketradar.providers.http import SafeHttpClient

log = get_logger(__name__)

SEC_SOURCE = ProviderSource(
    key="sec_edgar",
    name="SEC EDGAR",
    publisher="U.S. Securities and Exchange Commission",
    source_type=SourceType.SEC_FILING,
    source_class=SourceClass.REGULATORY,
    base_quality=100,
    homepage_url="https://www.sec.gov",
    is_synthetic=False,
    data_mode=DataMode.LIVE,
)

#: Filing forms that carry investment-relevant change. Others are ignored at this stage.
RELEVANT_FORMS = frozenset({"8-K", "10-Q", "10-K", "6-K", "20-F"})


class SecEdgarFilingsProvider:
    """Filings for a set of CIKs, retrieved from the EDGAR submissions API."""

    capability = ProviderCapability.FILINGS
    key = "sec_edgar"

    def __init__(
        self,
        settings: Settings | None = None,
        client: SafeHttpClient | None = None,
        ciks: tuple[str, ...] = (),
    ) -> None:
        self._settings = settings or get_settings()
        self._ciks = ciks
        self._client = client or SafeHttpClient(
            allowed_hosts=self._settings.http_allowed_hosts,
            timeout_seconds=self._settings.sec_request_timeout_seconds,
            # EDGAR's fair-access policy requires a descriptive User-Agent with contact info.
            headers={
                "User-Agent": self._settings.sec_user_agent or "MarketRadar (unconfigured)",
                "Accept-Encoding": "gzip, deflate",
            },
        )

    # ------------------------------------------------------------------
    def health(self) -> ProviderHealth:
        now = datetime.now(tz=UTC)
        if not self._settings.sec_user_agent:
            return ProviderHealth(
                provider_key=self.key,
                capability=self.capability,
                available=False,
                mode=DataMode.UNAVAILABLE,
                detail=(
                    "MARKETRADAR_SEC_USER_AGENT is not set. SEC EDGAR requires a "
                    "descriptive User-Agent with contact details before any request."
                ),
                checked_at=now,
                live_path_verified=False,
            )
        try:
            self._client.get_json(f"{self._settings.sec_base_url}/submissions/CIK0000320193.json")
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return ProviderHealth(
                provider_key=self.key,
                capability=self.capability,
                available=False,
                mode=DataMode.UNAVAILABLE,
                detail=f"SEC EDGAR unreachable: {exc}",
                checked_at=now,
                live_path_verified=False,
            )
        return ProviderHealth(
            provider_key=self.key,
            capability=self.capability,
            available=True,
            mode=DataMode.LIVE,
            detail="SEC EDGAR submissions API reachable.",
            checked_at=now,
            live_path_verified=True,
        )

    def sources(self) -> tuple[ProviderSource, ...]:
        return (SEC_SOURCE,)

    # ------------------------------------------------------------------
    def search(self, query: ProviderQuery) -> ProviderResult:
        """Return recent relevant filings for the configured CIKs.

        EDGAR's submissions API is per-issuer, not a text search: the ``query`` narrows by
        date and form, and text relevance is applied downstream by evidence extraction.
        """
        documents: list[ProviderDocument] = []
        for cik in self._ciks:
            try:
                documents.extend(self._filings_for_cik(cik, query))
            except ProviderError as exc:
                # One issuer failing must not fail the run.
                log.warning("sec_edgar.cik_failed", cik=cik, error=str(exc))
        documents.sort(key=lambda d: d.published_at, reverse=True)
        return ProviderResult(
            provider_key=self.key,
            mode=DataMode.LIVE,
            query=query.text,
            documents=tuple(documents[: query.limit]),
            truncated=len(documents) > query.limit,
        )

    def get_document(self, external_id: str) -> ProviderDocument | None:
        """Not supported: EDGAR documents are addressed by accession within an issuer."""
        return None

    # ------------------------------------------------------------------
    def _filings_for_cik(self, cik: str, query: ProviderQuery) -> list[ProviderDocument]:
        padded = cik.zfill(10)
        url = f"{self._settings.sec_base_url}/submissions/CIK{padded}.json"
        payload = self._client.get_json(url)
        return self.parse_submissions(payload, padded, query)

    @staticmethod
    def parse_submissions(
        payload: dict[str, Any], padded_cik: str, query: ProviderQuery
    ) -> list[ProviderDocument]:
        """Convert an EDGAR submissions payload into provider documents.

        Split out from transport so it can be unit-tested without network access.
        """
        recent = (payload.get("filings") or {}).get("recent") or {}
        forms: list[str] = recent.get("form", [])
        accessions: list[str] = recent.get("accessionNumber", [])
        filing_dates: list[str] = recent.get("filingDate", [])
        report_dates: list[str] = recent.get("reportDate", [])
        primary_docs: list[str] = recent.get("primaryDocument", [])
        descriptions: list[str] = recent.get("primaryDocDescription", [])
        entity = payload.get("name", "Unknown filer")

        documents: list[ProviderDocument] = []
        for index, form in enumerate(forms):
            if form not in RELEVANT_FORMS:
                continue
            try:
                filed = datetime.fromisoformat(filing_dates[index]).replace(tzinfo=UTC)
            except (IndexError, ValueError):
                continue
            if query.since and filed < query.since:
                continue
            if query.until and filed > query.until:
                continue

            report_date = report_dates[index] if index < len(report_dates) else ""
            try:
                event_at = (
                    datetime.fromisoformat(report_date).replace(tzinfo=UTC)
                    if report_date
                    else None
                )
            except ValueError:
                event_at = None

            accession = accessions[index] if index < len(accessions) else ""
            bare = accession.replace("-", "")
            primary = primary_docs[index] if index < len(primary_docs) else ""
            description = descriptions[index] if index < len(descriptions) else form
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(padded_cik)}/{bare}/{primary}"
                if bare and primary
                else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={padded_cik}"
            )
            documents.append(
                ProviderDocument(
                    external_id=f"sec:{padded_cik}:{accession}",
                    source_key=SEC_SOURCE.key,
                    url=url,
                    title=f"{entity} — {form} ({description})".strip(),
                    # The submissions index carries metadata only. The filing body is fetched
                    # separately; until then the body is the metadata line, never invented text.
                    body_text=f"{entity} filed a {form} ({description}) with the SEC.",
                    published_at=filed,
                    event_at=event_at,
                    origin_ref=f"sec:{padded_cik}:{accession}",
                    data_mode=DataMode.LIVE,
                    payload={
                        "form": form,
                        "accession_number": accession,
                        "cik": padded_cik,
                        "report_date": report_date,
                    },
                )
            )
        return documents
