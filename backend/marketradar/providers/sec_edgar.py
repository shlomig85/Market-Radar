"""SEC EDGAR filings provider.

Real adapter against ``data.sec.gov``. It is the reference implementation showing that the
provider interface fits a genuine external API rather than only a fixture.

**Verification status.** Every layer except the socket is executed by the test suite:
parsing, temporal mapping, document-text extraction, rate limiting and error handling are
covered against recorded-shape payloads through an injected transport. The live HTTP path
is *not* exercised in this development environment, where the egress proxy answers ``403``
to ``CONNECT`` for ``sec.gov`` under an organisation network policy. ``health()`` therefore
reports ``UNAVAILABLE`` with ``live_path_verified=False`` here, and only reports ``LIVE``
where the endpoint actually answers.

**Temporal contract.** EDGAR exposes three dates that must not be conflated:

* ``filingDate``  -> when the document became public  -> ``published_at`` (and knowability)
* ``reportDate``  -> the period the filing describes  -> ``event_at``
* ``acceptanceDateTime`` -> acceptance timestamp, used to order same-day filings

A 10-Q filed on 1 May for the quarter ended 31 March *occurred* in March but was not
knowable until May. Treating ``reportDate`` as publication would back-date knowability by
weeks and reintroduce the lookahead bias that ``Event.knowable_at`` exists to prevent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from marketradar.config import Settings, get_settings
from marketradar.domain.enums import DataMode, ProviderCapability, SourceClass, SourceType
from marketradar.errors import ProviderError
from marketradar.logging import get_logger
from marketradar.providers.base import (
    ProviderCompany,
    ProviderDocument,
    ProviderHealth,
    ProviderQuery,
    ProviderResult,
    ProviderSource,
)
from marketradar.providers.http import RateLimiter, SafeHttpClient
from marketradar.providers.sec_text import extract_text, prose_only

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

#: SEC fair-access policy. Exceeding this gets a client blocked, so it is enforced rather
#: than hoped for.
SEC_MAX_REQUESTS_PER_SECOND = 10

#: Filing bodies are fetched one HTTP request at a time; this bounds a single search.
DEFAULT_MAX_DOCUMENT_FETCHES = 40


def _build_client(settings: Settings) -> SafeHttpClient:
    """One place that knows how to talk to SEC politely."""
    return SafeHttpClient(
        allowed_hosts=settings.http_allowed_hosts,
        timeout_seconds=settings.sec_request_timeout_seconds,
        # EDGAR's fair-access policy requires a descriptive User-Agent with contact details;
        # requests without one are refused outright.
        headers={
            "User-Agent": settings.sec_user_agent or "MarketRadar (unconfigured)",
            "Accept-Encoding": "gzip, deflate",
        },
        rate_limiter=RateLimiter(SEC_MAX_REQUESTS_PER_SECOND, 1.0),
    )


def _unconfigured_health(key: str, capability: ProviderCapability) -> ProviderHealth:
    return ProviderHealth(
        provider_key=key,
        capability=capability,
        available=False,
        mode=DataMode.UNAVAILABLE,
        detail=(
            "MARKETRADAR_SEC_USER_AGENT is not set. SEC EDGAR requires a descriptive "
            "User-Agent with contact details before any request; without it the adapter "
            "will not call the API."
        ),
        checked_at=datetime.now(tz=UTC),
        live_path_verified=False,
    )


class SecCompanyProvider:
    """Real US registrants from SEC's published company/ticker file.

    This is the reference universe entity resolution needs: every CIK, ticker and legal
    name the SEC recognises. Free, no key, one request.
    """

    capability = ProviderCapability.COMPANY_DATA
    key = "sec_company"

    #: Includes exchange when available; falls back to the smaller file if not.
    TICKERS_WITH_EXCHANGE = "https://www.sec.gov/files/company_tickers_exchange.json"
    TICKERS = "https://www.sec.gov/files/company_tickers.json"

    def __init__(
        self, settings: Settings | None = None, client: SafeHttpClient | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or _build_client(self._settings)
        self._cache: tuple[ProviderCompany, ...] | None = None

    def health(self) -> ProviderHealth:
        if not self._settings.sec_user_agent:
            return _unconfigured_health(self.key, self.capability)
        try:
            self.list_companies()
        except Exception as exc:  # noqa: BLE001 - health must never raise
            return ProviderHealth(
                provider_key=self.key,
                capability=self.capability,
                available=False,
                mode=DataMode.UNAVAILABLE,
                detail=f"SEC company file unreachable: {exc}",
                checked_at=datetime.now(tz=UTC),
                live_path_verified=False,
            )
        return ProviderHealth(
            provider_key=self.key,
            capability=self.capability,
            available=True,
            mode=DataMode.LIVE,
            detail=f"SEC company file reachable; {len(self._cache or ())} registrants.",
            checked_at=datetime.now(tz=UTC),
            live_path_verified=True,
        )

    def list_companies(self) -> tuple[ProviderCompany, ...]:
        if self._cache is not None:
            return self._cache
        try:
            payload = self._client.get_json(self.TICKERS_WITH_EXCHANGE)
        except ProviderError:
            payload = self._client.get_json(self.TICKERS)
        self._cache = self.parse_company_file(payload)
        return self._cache

    def get_company(self, key: str) -> ProviderCompany | None:
        wanted = key.lower()
        for company in self.list_companies():
            if company.key == wanted or (company.ticker or "").lower() == wanted:
                return company
        return None

    @staticmethod
    def parse_company_file(payload: Any) -> tuple[ProviderCompany, ...]:
        """Parse either SEC company-ticker file shape.

        Two formats exist and both are in active use:
        ``{"fields": [...], "data": [[...], ...]}`` and
        ``{"0": {"cik_str": ..., "ticker": ..., "title": ...}, ...}``.
        """
        rows: list[dict[str, Any]] = []
        if isinstance(payload, dict) and "fields" in payload and "data" in payload:
            fields = [str(f).lower() for f in payload["fields"]]
            for record in payload["data"]:
                rows.append(dict(zip(fields, record, strict=False)))
        elif isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, dict):
                    rows.append({str(k).lower(): v for k, v in value.items()})

        companies: list[ProviderCompany] = []
        seen: set[str] = set()
        for row in rows:
            cik_raw = row.get("cik_str") or row.get("cik")
            ticker = str(row.get("ticker") or "").strip().upper()
            name = str(row.get("title") or row.get("name") or "").strip()
            if cik_raw in (None, "") or not name:
                continue
            cik = str(cik_raw).strip().zfill(10)
            # One issuer can list several share classes; each is a distinct security but the
            # same company. Keyed by CIK so they collapse rather than duplicating the issuer.
            key = f"sec-{cik}"
            if key in seen:
                continue
            seen.add(key)
            companies.append(
                ProviderCompany(
                    key=key,
                    name=name,
                    ticker=ticker or None,
                    exchange=str(row.get("exchange") or "").strip().upper() or None,
                    cik=cik,
                    country="US",
                    is_fictional=False,
                    data_mode=DataMode.LIVE,
                )
            )
        return tuple(companies)


class SecEdgarFilingsProvider:
    """Filings for a set of CIKs, retrieved from the EDGAR submissions API."""

    capability = ProviderCapability.FILINGS
    key = "sec_edgar"

    def __init__(
        self,
        settings: Settings | None = None,
        client: SafeHttpClient | None = None,
        ciks: tuple[str, ...] = (),
        fetch_documents: bool = True,
        max_document_fetches: int = DEFAULT_MAX_DOCUMENT_FETCHES,
    ) -> None:
        self._settings = settings or get_settings()
        self._ciks = ciks
        self._fetch_documents = fetch_documents
        self._max_document_fetches = max_document_fetches
        self._client = client or _build_client(self._settings)

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
        selected = documents[: query.limit]

        # The submissions index carries metadata only. Without the filing body there is no
        # prose to extract evidence from, which is why the previous version of this adapter
        # produced no usable signal. Fetching the document is the point of the provider.
        if self._fetch_documents:
            selected = self._attach_bodies(selected)

        return ProviderResult(
            provider_key=self.key,
            mode=DataMode.LIVE,
            query=query.text,
            documents=tuple(selected),
            truncated=len(documents) > query.limit,
        )

    def _attach_bodies(self, documents: list[ProviderDocument]) -> list[ProviderDocument]:
        """Fetch each filing's primary document and replace the metadata stub with its text.

        A filing whose body cannot be fetched is **dropped**, not kept with placeholder
        text: an evidence item extracted from a stub sentence we wrote ourselves would be
        indistinguishable from one extracted from the issuer's own words.
        """
        enriched: list[ProviderDocument] = []
        for index, document in enumerate(documents):
            if index >= self._max_document_fetches:
                log.info("sec_edgar.document_fetch_capped", cap=self._max_document_fetches)
                break
            try:
                raw = self._client.get_text(document.url)
            except ProviderError as exc:
                log.warning(
                    "sec_edgar.document_unavailable", url=document.url, error=str(exc)
                )
                continue

            body = prose_only(extract_text(raw))
            if len(body.split()) < 50:
                log.info("sec_edgar.document_too_short", url=document.url)
                continue
            enriched.append(document.model_copy(update={"body_text": body}))
        return enriched

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
