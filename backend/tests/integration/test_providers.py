"""Provider contracts, including the honest-unavailability contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from marketradar.config import Settings
from marketradar.domain.enums import DataMode, ProviderCapability
from marketradar.errors import UnsafeUrlError
from marketradar.providers import build_default_registry
from marketradar.providers.base import ProviderQuery
from marketradar.providers.fixture import (
    FixtureCompanyProvider,
    FixtureFilingsProvider,
    FixtureNewsProvider,
)
from marketradar.providers.http import SafeHttpClient
from marketradar.providers.sec_edgar import SecEdgarFilingsProvider
from marketradar.providers.unavailable import UnavailableProvider


def test_registry_reports_every_capability_including_missing_ones():
    report = build_default_registry(Settings()).health_report()
    assert {h.capability for h in report} == set(ProviderCapability)


def test_market_data_is_reported_unavailable_not_faked():
    registry = build_default_registry(Settings())
    health = registry.market_data.health()
    assert health.mode == DataMode.UNAVAILABLE
    assert not health.available
    assert "No market-data provider is configured" in health.detail
    # The message must say what is *not* computed, so the gap is legible to a user.
    assert "reported as unavailable rather than estimated" in health.detail


def test_an_unavailable_provider_returns_a_typed_answer_not_silence():
    """A caller must be able to record *why* an input was missing."""
    provider = UnavailableProvider(ProviderCapability.MARKET_DATA, "no vendor")
    snapshot = provider.get_snapshot("NBMX")
    assert snapshot is not None
    assert snapshot.mode == DataMode.UNAVAILABLE
    assert snapshot.price is None
    assert snapshot.detail == "no vendor"


def test_an_unavailable_search_returns_unavailable_not_an_empty_result():
    provider = UnavailableProvider(ProviderCapability.NEWS_SEARCH, "no vendor")
    result = provider.search(ProviderQuery(text="memory"))
    assert result.mode == DataMode.UNAVAILABLE
    assert result.count == 0


def test_fixture_providers_are_demo_mode_and_say_so():
    for provider in (FixtureNewsProvider(), FixtureFilingsProvider(), FixtureCompanyProvider()):
        health = provider.health()
        assert health.mode == DataMode.DEMO
        assert "synthetic" in health.detail.lower() or "fictional" in health.detail.lower()


def test_every_demo_company_is_fictional():
    assert all(c.is_fictional for c in FixtureCompanyProvider().list_companies())


def test_every_demo_document_is_on_a_non_resolvable_domain():
    for provider in (FixtureNewsProvider(), FixtureFilingsProvider()):
        for document in provider.get_recent_documents(limit=500).documents:
            assert ".invalid" in document.url, document.url
            assert document.data_mode == DataMode.DEMO


def test_news_and_filings_capabilities_serve_different_documents():
    news = {d.external_id for d in FixtureNewsProvider().get_recent_documents(500).documents}
    filings = {d.external_id for d in FixtureFilingsProvider().get_recent_documents(500).documents}
    assert news and filings
    assert not (news & filings)


def test_search_respects_the_date_window():
    provider = FixtureNewsProvider()
    cutoff = datetime(2026, 8, 20, tzinfo=UTC)
    result = provider.search(ProviderQuery(text="memory demand pricing", since=cutoff, limit=50))
    assert result.documents
    assert all((d.event_at or d.published_at) >= cutoff for d in result.documents)


# ---------------------------------------------------------------- SEC adapter
def test_sec_provider_is_unavailable_without_a_user_agent():
    """EDGAR's fair-access policy requires a contact User-Agent; without it we do not call."""
    provider = SecEdgarFilingsProvider(settings=Settings(sec_user_agent=""))
    health = provider.health()
    assert health.mode == DataMode.UNAVAILABLE
    assert not health.live_path_verified
    assert "USER_AGENT" in health.detail


def test_sec_submissions_parsing(monkeypatch):
    """The parsing layer is testable without network access, which is the whole point of
    keeping transport separate."""
    payload = {
        "name": "EXAMPLE FILER INC",
        "filings": {
            "recent": {
                "form": ["8-K", "4", "10-Q"],
                "accessionNumber": ["0000320193-26-000001", "x", "0000320193-26-000002"],
                "filingDate": ["2026-08-01", "2026-08-02", "2026-07-15"],
                "reportDate": ["2026-07-30", "", "2026-06-30"],
                "primaryDocument": ["a8k.htm", "f4.xml", "b10q.htm"],
                "primaryDocDescription": ["8-K", "FORM 4", "10-Q"],
            }
        },
    }
    documents = SecEdgarFilingsProvider.parse_submissions(
        payload, "0000320193", ProviderQuery(text="", limit=10)
    )
    # Form 4 is not investment-relevant here and is filtered out.
    assert [d.payload["form"] for d in documents] == ["8-K", "10-Q"]
    first = documents[0]
    assert first.data_mode == DataMode.LIVE
    assert first.published_at.date().isoformat() == "2026-08-01"
    # Report date and filing date are different facts and stay separate.
    assert first.event_at is not None and first.event_at.date().isoformat() == "2026-07-30"
    assert "sec.gov" in first.url
    assert first.origin_ref == "sec:0000320193:0000320193-26-000001"


def test_sec_parsing_survives_a_truncated_payload():
    payload = {"name": "X", "filings": {"recent": {"form": ["8-K"], "filingDate": []}}}
    assert SecEdgarFilingsProvider.parse_submissions(
        payload, "0000000001", ProviderQuery(text="", limit=5)
    ) == []


# ---------------------------------------------------------------- SSRF
def test_outbound_requests_are_restricted_to_allowlisted_https_hosts():
    client = SafeHttpClient(allowed_hosts=["data.sec.gov"])
    client.check_url("https://data.sec.gov/submissions/CIK0000320193.json")
    with pytest.raises(UnsafeUrlError):
        client.check_url("https://evil.example.com/x")
    with pytest.raises(UnsafeUrlError):
        client.check_url("http://data.sec.gov/x")
    with pytest.raises(UnsafeUrlError):
        client.check_url("https://169.254.169.254/latest/meta-data/")
    client.close()
