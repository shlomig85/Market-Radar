"""Real SEC provider behaviour, exercised through an injected transport.

Everything except the socket runs here: parsing, the temporal mapping, document-text
extraction, rate limiting, size caps and every failure path. The live HTTP call itself
cannot run in this environment — the egress proxy denies CONNECT to sec.gov under network
policy — so it is verified separately on an unrestricted machine.
"""

from __future__ import annotations

from datetime import UTC

import httpx
import pytest

from marketradar.config import Settings
from marketradar.domain.enums import DataMode
from marketradar.errors import ProviderError
from marketradar.providers.base import ProviderQuery
from marketradar.providers.http import RateLimiter, SafeHttpClient
from marketradar.providers.sec_edgar import SecCompanyProvider, SecEdgarFilingsProvider
from marketradar.providers.sec_text import extract_text, prose_only

SETTINGS = Settings(sec_user_agent="Market Radar test@example.com")


def transport(routes: dict[str, tuple[int, str]]) -> httpx.MockTransport:
    """Serve recorded payloads by URL, 404 for anything unexpected."""

    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(str(request.url), (404, "not found"))
        return httpx.Response(status, text=body)

    return httpx.MockTransport(handler)


def client_for(routes: dict[str, tuple[int, str]], **kwargs) -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=["data.sec.gov", "www.sec.gov"],
        transport=transport(routes),
        **kwargs,
    )


# ------------------------------------------------------------------ company universe
COMPANY_FILE = """
{"fields":["cik","name","ticker","exchange"],
 "data":[[320193,"Apple Inc.","AAPL","Nasdaq"],
         [1652044,"Alphabet Inc.","GOOGL","Nasdaq"],
         [1652044,"Alphabet Inc.","GOOG","Nasdaq"],
         [1090727,"UNITED PARCEL SERVICE INC","UPS","NYSE"]]}
"""


def test_company_file_produces_real_live_issuers():
    provider = SecCompanyProvider(
        SETTINGS, client_for({SecCompanyProvider.TICKERS_WITH_EXCHANGE: (200, COMPANY_FILE)})
    )
    companies = provider.list_companies()
    assert {c.ticker for c in companies} == {"AAPL", "GOOGL", "UPS"}
    for company in companies:
        assert company.data_mode == DataMode.LIVE
        assert company.is_fictional is False
        assert company.cik and len(company.cik) == 10


def test_share_classes_collapse_to_one_issuer():
    """GOOG and GOOGL are two securities but one company (PRD 76)."""
    provider = SecCompanyProvider(
        SETTINGS, client_for({SecCompanyProvider.TICKERS_WITH_EXCHANGE: (200, COMPANY_FILE)})
    )
    alphabet = [c for c in provider.list_companies() if c.cik == "0001652044"]
    assert len(alphabet) == 1


def test_company_provider_falls_back_to_the_legacy_file():
    routes = {
        SecCompanyProvider.TICKERS_WITH_EXCHANGE: (500, "boom"),
        SecCompanyProvider.TICKERS: (
            200,
            '{"0":{"cik_str":789019,"ticker":"MSFT","title":"MICROSOFT CORP"}}',
        ),
    }
    provider = SecCompanyProvider(SETTINGS, client_for(routes))
    assert [c.ticker for c in provider.list_companies()] == ["MSFT"]


def test_company_provider_is_unavailable_without_a_user_agent():
    provider = SecCompanyProvider(Settings(sec_user_agent=""), client_for({}))
    health = provider.health()
    assert health.mode == DataMode.UNAVAILABLE
    assert health.live_path_verified is False
    assert "USER_AGENT" in health.detail


def test_company_provider_health_reports_unavailable_when_unreachable():
    provider = SecCompanyProvider(SETTINGS, client_for({}))
    health = provider.health()
    assert health.mode == DataMode.UNAVAILABLE
    assert health.available is False


# ------------------------------------------------------------------ temporal contract
SUBMISSIONS = """
{"name":"EXAMPLE MEMORY CORP",
 "filings":{"recent":{
   "form":["10-Q","4","8-K"],
   "accessionNumber":["0000320193-26-000010","0000320193-26-000011","0000320193-26-000012"],
   "filingDate":["2026-05-01","2026-05-02","2026-05-03"],
   "reportDate":["2026-03-31","","2026-05-03"],
   "primaryDocument":["q.htm","f4.xml","k.htm"],
   "primaryDocDescription":["10-Q","FORM 4","8-K"]}}}
"""


def _filings(query: ProviderQuery | None = None, **kwargs):
    routes = {"https://data.sec.gov/submissions/CIK0000320193.json": (200, SUBMISSIONS)}
    routes.update(kwargs.pop("routes", {}))
    provider = SecEdgarFilingsProvider(
        SETTINGS, client_for(routes), ciks=("320193",), **kwargs
    )
    return provider.search(query or ProviderQuery(text="", limit=10))


def test_filing_date_is_publication_and_report_date_is_the_event():
    """The single most important temporal rule for real filings.

    A 10-Q filed 1 May for the quarter ended 31 March occurred in March but was not
    knowable until May. Swapping these back-dates knowability by weeks and reintroduces
    lookahead bias.
    """
    documents = _filings(fetch_documents=False).documents
    quarterly = next(d for d in documents if d.payload["form"] == "10-Q")
    assert quarterly.published_at.date().isoformat() == "2026-05-01"
    assert quarterly.event_at is not None
    assert quarterly.event_at.date().isoformat() == "2026-03-31"
    assert quarterly.event_at < quarterly.published_at


def test_irrelevant_forms_are_filtered_out():
    forms = {d.payload["form"] for d in _filings(fetch_documents=False).documents}
    assert forms == {"10-Q", "8-K"}
    assert "4" not in forms


def test_filings_are_live_mode_and_carry_an_origin_ref():
    documents = _filings(fetch_documents=False).documents
    assert documents
    for document in documents:
        assert document.data_mode == DataMode.LIVE
        assert document.origin_ref and document.origin_ref.startswith("sec:")


def test_date_window_is_applied_to_the_filing_date():
    from datetime import datetime

    result = _filings(
        ProviderQuery(text="", limit=10, until=datetime(2026, 5, 2, tzinfo=UTC)),
        fetch_documents=False,
    )
    assert {d.payload["form"] for d in result.documents} == {"10-Q"}


def test_truncated_payload_does_not_raise():
    routes = {
        "https://data.sec.gov/submissions/CIK0000320193.json": (
            200,
            '{"name":"X","filings":{"recent":{"form":["8-K"],"filingDate":[]}}}',
        )
    }
    provider = SecEdgarFilingsProvider(SETTINGS, client_for(routes), ciks=("320193",))
    assert provider.search(ProviderQuery(text="", limit=5)).documents == ()


def test_one_failing_issuer_does_not_fail_the_whole_search():
    routes = {"https://data.sec.gov/submissions/CIK0000320193.json": (200, SUBMISSIONS)}
    provider = SecEdgarFilingsProvider(
        SETTINGS, client_for(routes), ciks=("320193", "999999"), fetch_documents=False
    )
    assert provider.search(ProviderQuery(text="", limit=10)).count == 2


# ------------------------------------------------------------------ document text
# Shaped like a real filing: inline XBRL header, hard-wrapped prose (which is what broke
# span-based extraction until sentences were collapsed before matching), and a numeric table.
FILING_HTML = """<html><body>
<ix:header><ix:hidden>MACHINE ONLY 999</ix:hidden></ix:header>
<p>Item 2. Management&#39;s Discussion and Analysis of Financial Condition and Results of
Operations. The following discussion should be read together with the condensed
consolidated financial statements and related notes included elsewhere in this report.</p>
<p>During the quarter the Company experienced increasing demand for memory products from
artificial intelligence server customers, and order backlog increased compared with the
prior quarter. Management believes this reflects broader deployment of accelerated
computing infrastructure by cloud service providers.</p>
<p>The Company is increasing capital expenditure to add capacity and notes that inventory
declined during the period. Contract pricing improved versus the prior quarter, and the
Company extended several supply agreements with existing customers.</p>
<table><tr><td>2026</td><td>1,234</td><td>5,678</td></tr>
<tr><td>2025</td><td>1,100</td><td>5,000</td></tr></table>
</body></html>"""


def test_filing_body_is_fetched_and_reduced_to_prose():
    routes = {
        "https://data.sec.gov/submissions/CIK0000320193.json": (200, SUBMISSIONS),
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/q.htm": (
            200,
            FILING_HTML,
        ),
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000012/k.htm": (
            200,
            FILING_HTML,
        ),
    }
    documents = _filings(routes=routes).documents
    assert documents
    body = documents[0].body_text
    assert "increasing demand for memory products" in body
    assert "MACHINE ONLY" not in body, "inline XBRL must not leak into evidence"
    assert "1,234" not in body, "table rows are not prose"


def test_a_filing_whose_body_cannot_be_fetched_is_dropped_not_stubbed():
    """A stub sentence we wrote would be indistinguishable from the issuer's own words."""
    routes = {"https://data.sec.gov/submissions/CIK0000320193.json": (200, SUBMISSIONS)}
    assert _filings(routes=routes).documents == ()


def test_extracted_filing_text_yields_real_evidence():
    """End of the chain: real filing prose must produce extractable events."""
    from marketradar.evidence.extractor import extract

    body = prose_only(extract_text(FILING_HTML))
    events = [e for e in extract(body, subject_hints=("memory",)) if e.event_type]
    assert {e.event_type.value for e in events} >= {
        "DEMAND_ACCELERATION",
        "CAPEX_INCREASE",
        "INVENTORY_DECLINE",
        "PRICING_INCREASE",
    }


# ------------------------------------------------------------------ safety
def test_rate_limiter_enforces_the_sec_cap_deterministically():
    now = [0.0]
    rl = RateLimiter(
        10, 1.0, clock=lambda: now[0], sleeper=lambda s: now.__setitem__(0, now[0] + s)
    )
    for _ in range(10):
        assert rl.acquire() == 0.0
    assert rl.acquire() == pytest.approx(1.0), "the 11th request in a second must wait"


def test_oversized_responses_are_refused():
    routes = {"https://data.sec.gov/big.json": (200, "x" * 5000)}
    client = client_for(routes, max_response_bytes=1000)
    with pytest.raises(ProviderError, match="maximum allowed size"):
        client.get_json("https://data.sec.gov/big.json")


def test_non_json_response_is_a_provider_error_not_a_crash():
    client = client_for({"https://data.sec.gov/x.json": (200, "<html>maintenance</html>")})
    with pytest.raises(ProviderError, match="not valid JSON"):
        client.get_json("https://data.sec.gov/x.json")
