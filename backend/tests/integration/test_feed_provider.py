"""Reliable-source web ingestion (RSS/Atom).

Before this provider, the only news the system could see was the synthetic development
corpus, so it could not discover anything about the real world — it could only monitor a
watchlist of CIKs someone had typed in.

Tested against recorded-shape payloads through an injected transport, so every layer except
the socket runs here. The live path is verified by the operator with `live-check`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from marketradar.domain.enums import DataMode, SourceClass, SourceType
from marketradar.errors import UnsafeUrlError
from marketradar.providers.feeds import (
    DEFAULT_FEEDS,
    FeedDescriptor,
    RssFeedProvider,
    parse_datetime,
    parse_feed,
    parse_feed_spec,
)
from marketradar.providers.http import SafeHttpClient

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Example Wire</title>
    <item>
      <title>Memory prices climb as AI server demand accelerates</title>
      <link>https://example.test/articles/memory-prices</link>
      <guid>https://example.test/articles/memory-prices</guid>
      <pubDate>Tue, 08 Sep 2026 14:30:00 GMT</pubDate>
      <dc:creator>A Reporter</dc:creator>
      <description>Contract pricing for memory rose again this quarter as buyers
        competed for constrained supply, according to three distributors.</description>
    </item>
    <item>
      <title>An item with no date at all</title>
      <link>https://example.test/articles/undated</link>
      <description>This should never be ingested.</description>
    </item>
  </channel>
</rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Journal</title>
  <entry>
    <title>Lithography tool lead times extend into next year</title>
    <link href="https://journal.test/posts/litho"/>
    <id>tag:journal.test,2026:litho</id>
    <published>2026-09-07T09:00:00Z</published>
    <summary>Equipment makers report that lead times for advanced lithography tools
      have extended, with capacity substantially committed through the year.</summary>
  </entry>
</feed>
"""

ARTICLE = (
    "<html><body><article><p>"
    + " ".join(["Memory contract pricing rose across every grade this quarter."] * 30)
    + "</p></article></body></html>"
)

FEED = FeedDescriptor(
    key="example-wire",
    name="Example Wire",
    publisher="Example",
    url="https://example.test/feed.xml",
    source_type=SourceType.MAJOR_FINANCIAL_MEDIA,
    source_class=SourceClass.FINANCIAL_MEDIA,
    base_quality=75,
)


def _client(handler, hosts=("example.test", "journal.test")) -> SafeHttpClient:
    return SafeHttpClient(
        allowed_hosts=list(hosts),
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "Market Radar test@example.com"},
    )


def _serving(**routes: str):
    def handler(request: httpx.Request) -> httpx.Response:
        body = routes.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body)

    return handler


# ------------------------------------------------------------------ parsing
def test_rss_items_are_parsed_with_their_publication_time() -> None:
    items = parse_feed(RSS)
    assert len(items) == 1  # the undated item is dropped
    item = items[0]
    assert item.title == "Memory prices climb as AI server demand accelerates"
    assert item.url == "https://example.test/articles/memory-prices"
    assert item.published_at == datetime(2026, 9, 8, 14, 30, tzinfo=UTC)
    assert item.author == "A Reporter"


def test_atom_entries_are_parsed_too() -> None:
    """Both shapes are common; a provider that handles one is half a provider."""
    items = parse_feed(ATOM)
    assert len(items) == 1
    assert items[0].url == "https://journal.test/posts/litho"
    assert items[0].published_at == datetime(2026, 9, 7, 9, 0, tzinfo=UTC)


def test_an_undated_item_is_never_ingested() -> None:
    """An item that cannot be placed on a timeline cannot be read as-of safely."""
    assert parse_feed(
        '<rss version="2.0"><channel><item><title>T</title>'
        "<link>https://example.test/x</link></item></channel></rss>"
    ) == []


@pytest.mark.parametrize(
    "raw",
    [
        "Tue, 08 Sep 2026 14:30:00 GMT",
        "2026-09-08T14:30:00Z",
        "2026-09-08T14:30:00+00:00",
        "2026-09-08T16:30:00+0200",
    ],
)
def test_the_date_formats_feeds_actually_use_are_all_understood(raw: str) -> None:
    parsed = parse_datetime(raw)
    assert parsed is not None
    assert parsed.astimezone(UTC) == datetime(2026, 9, 8, 14, 30, tzinfo=UTC)


def test_an_unparseable_date_returns_none_rather_than_now() -> None:
    """Defaulting to 'now' would make an old article look like breaking news."""
    assert parse_datetime("last Tuesday") is None
    assert parse_datetime(None) is None


def test_malformed_xml_raises_rather_than_returning_nothing() -> None:
    with pytest.raises(ValueError, match="not well-formed"):
        parse_feed("<rss><channel><item>")


# ------------------------------------------------------------- retrieval
def test_the_article_body_is_fetched_not_just_the_summary() -> None:
    provider = RssFeedProvider(
        _client(
            _serving(
                **{
                    "https://example.test/feed.xml": RSS,
                    "https://example.test/articles/memory-prices": ARTICLE,
                }
            )
        ),
        feeds=(FEED,),
    )
    result = provider.get_recent_documents()
    assert result.mode is DataMode.LIVE
    assert result.count == 1
    document = result.documents[0]
    assert document.payload["body_source"] == "article"
    assert len(document.body_text.split()) > 120
    assert document.data_mode is DataMode.LIVE


def test_an_unreachable_article_falls_back_to_the_publishers_own_summary() -> None:
    """A paywall is not a reason to fabricate, and an abstract is real text."""
    provider = RssFeedProvider(
        _client(_serving(**{"https://example.test/feed.xml": RSS})),
        feeds=(FEED,),
    )
    document = provider.get_recent_documents().documents[0]
    assert document.payload["body_source"] == "summary"
    assert "constrained supply" in document.body_text


def test_event_time_is_left_unstated_rather_than_assumed() -> None:
    """A feed says when it PUBLISHED, never when the thing happened (ADR-013)."""
    provider = RssFeedProvider(
        _client(_serving(**{"https://example.test/feed.xml": RSS})), feeds=(FEED,)
    )
    assert provider.get_recent_documents().documents[0].event_at is None


def test_items_published_after_the_as_of_instant_are_never_returned() -> None:
    """Windowed at the source: the lookahead fix, applied to the news path."""
    provider = RssFeedProvider(
        _client(_serving(**{"https://example.test/feed.xml": RSS})), feeds=(FEED,)
    )
    before = provider.get_recent_documents(until=datetime(2026, 9, 1, tzinfo=UTC))
    assert before.count == 0
    after = provider.get_recent_documents(until=datetime(2026, 9, 30, tzinfo=UTC))
    assert after.count == 1


# ------------------------------------------------------------ degradation
def test_one_broken_feed_does_not_stop_the_others() -> None:
    good = FEED
    broken = FeedDescriptor(
        key="broken",
        name="Broken",
        publisher="Nobody",
        url="https://journal.test/missing.xml",
        source_type=SourceType.SPECIALIST_PUBLICATION,
        source_class=SourceClass.INDUSTRY,
        base_quality=50,
    )
    provider = RssFeedProvider(
        _client(_serving(**{"https://example.test/feed.xml": RSS})),
        feeds=(good, broken),
    )
    result = provider.get_recent_documents()
    assert result.mode is DataMode.LIVE
    assert result.count == 1
    assert result.detail and "1/2 feeds answered" in result.detail


def test_every_feed_failing_is_unavailable_not_an_empty_result() -> None:
    """'No articles today' and 'every publisher is down' are different facts."""
    provider = RssFeedProvider(_client(_serving()), feeds=(FEED,))
    result = provider.get_recent_documents()
    assert result.mode is DataMode.UNAVAILABLE
    assert result.count == 0
    assert result.detail and "could be reached" in result.detail


def test_no_subscribed_feeds_is_reported_as_unavailable() -> None:
    provider = RssFeedProvider(_client(_serving()), feeds=())
    health = provider.health()
    assert not health.available
    assert health.mode is DataMode.UNAVAILABLE
    assert "MARKETRADAR_FEEDS" in health.detail


# -------------------------------------------------------------- security
def test_a_feed_host_outside_the_allowlist_is_refused() -> None:
    provider = RssFeedProvider(
        _client(_serving(), hosts=("example.test",)),
        feeds=(
            FeedDescriptor(
                key="elsewhere",
                name="Elsewhere",
                publisher="Elsewhere",
                url="https://elsewhere.test/feed.xml",
                source_type=SourceType.SPECIALIST_PUBLICATION,
                source_class=SourceClass.INDUSTRY,
                base_quality=40,
            ),
        ),
    )
    assert provider.get_recent_documents().mode is DataMode.UNAVAILABLE


def test_a_redirect_off_the_allowlist_is_refused() -> None:
    """Redirects are followed, so every hop must be checked, not just the first."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.test/feed.xml":
            return httpx.Response(302, headers={"location": "https://evil.test/feed.xml"})
        return httpx.Response(200, text=RSS)

    client = _client(handler, hosts=("example.test",))
    with pytest.raises(UnsafeUrlError):
        client.get_text("https://example.test/feed.xml")


def test_a_redirect_within_the_allowlist_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.test/feed.xml":
            return httpx.Response(301, headers={"location": "/feed-v2.xml"})
        return httpx.Response(200, text=RSS)

    client = _client(handler, hosts=("example.test",))
    assert "Example Wire" in client.get_text("https://example.test/feed.xml")


def test_a_redirect_loop_terminates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.test/loop"})

    client = _client(handler, hosts=("example.test",))
    with pytest.raises(Exception, match="Too many redirects"):
        client.get_text("https://example.test/loop")


# ------------------------------------------------------------ descriptors
def test_the_default_feed_list_is_well_formed() -> None:
    """These endpoints are unverified from the build environment; their shape is not."""
    assert DEFAULT_FEEDS
    keys = [feed.key for feed in DEFAULT_FEEDS]
    assert len(keys) == len(set(keys)), "feed keys must be unique"
    for feed in DEFAULT_FEEDS:
        assert feed.url.startswith("https://"), feed.key
        assert 0 <= feed.base_quality <= 100
    # A primary or statutory source must outrank general media, because independence
    # scoring uses these priors to decide what counts as corroboration.
    government = [f for f in DEFAULT_FEEDS if f.source_class is SourceClass.GOVERNMENT]
    media = [f for f in DEFAULT_FEEDS if f.source_class is SourceClass.FINANCIAL_MEDIA]
    assert min(f.base_quality for f in government) > max(f.base_quality for f in media)


def test_a_feed_can_be_configured_without_touching_the_code() -> None:
    """'The reliable ones' is the operator's judgement, not a constant in this repo."""
    feed = parse_feed_spec(
        "myblog|My Blog|Someone|https://blog.test/rss|SPECIALIST_PUBLICATION|INDUSTRY|65"
    )
    assert feed.key == "myblog"
    assert feed.url == "https://blog.test/rss"
    assert feed.base_quality == 65
    assert feed.fetch_article is True


def test_a_malformed_feed_spec_fails_loudly() -> None:
    with pytest.raises(ValueError, match="7 pipe-separated fields"):
        parse_feed_spec("myblog|My Blog|https://blog.test/rss")


# ------------------------------------------------------------------- probe
def test_probe_reports_each_feed_separately() -> None:
    """A dead feed must be visible, not silent — silence looks like a quiet news day."""
    broken = FeedDescriptor(
        key="broken",
        name="Broken",
        publisher="Nobody",
        url="https://journal.test/missing.xml",
        source_type=SourceType.SPECIALIST_PUBLICATION,
        source_class=SourceClass.INDUSTRY,
        base_quality=50,
    )
    provider = RssFeedProvider(
        _client(_serving(**{"https://example.test/feed.xml": RSS})),
        feeds=(FEED, broken),
    )
    probes = {probe.feed.key: probe for probe in provider.probe()}

    assert probes["example-wire"].reachable
    assert probes["example-wire"].item_count == 1
    assert probes["example-wire"].newest == datetime(2026, 9, 8, 14, 30, tzinfo=UTC)
    assert probes["example-wire"].status == "ok"

    assert not probes["broken"].reachable
    assert probes["broken"].status.startswith("FAILED")


def test_probe_distinguishes_empty_from_unreachable() -> None:
    """A publisher with nothing new and a publisher that is gone are different facts."""
    provider = RssFeedProvider(
        _client(
            _serving(
                **{
                    "https://example.test/feed.xml": (
                        '<rss version="2.0"><channel><title>Quiet</title></channel></rss>'
                    )
                }
            )
        ),
        feeds=(FEED,),
    )
    probe = provider.probe()[0]
    assert probe.reachable
    assert probe.item_count == 0
    assert probe.status == "reachable but empty"


def test_probe_never_fetches_articles() -> None:
    """It must stay cheap enough to run whenever something looks wrong."""
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        if str(request.url) == "https://example.test/feed.xml":
            return httpx.Response(200, text=RSS)
        return httpx.Response(200, text=ARTICLE)

    RssFeedProvider(_client(handler), feeds=(FEED,)).probe()
    assert fetched == ["https://example.test/feed.xml"]


def test_the_default_list_is_weighted_toward_free_primary_sources() -> None:
    """Free and public is a hard constraint; reliability is bought with source class."""
    government = [
        f
        for f in DEFAULT_FEEDS
        if f.source_class in (SourceClass.GOVERNMENT, SourceClass.REGULATORY)
    ]
    assert len(government) >= len(DEFAULT_FEEDS) / 2, (
        "statutory bodies publish the numbers everyone else reports on; they should carry "
        "the list"
    )
    # No entry may need a key or a subscription: the URL must be fetchable as-is.
    for feed in DEFAULT_FEEDS:
        assert "api_key" not in feed.url and "apikey" not in feed.url.lower(), feed.key
        assert "token" not in feed.url.lower(), feed.key
