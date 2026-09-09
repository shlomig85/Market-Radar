"""Reliable-source web ingestion: RSS and Atom feeds.

Until this existed, the only news the system could see was the synthetic development
corpus, so it could not discover anything about the actual world. A watchlist of CIKs told
it which companies to read about; nothing told it what was *happening*.

Design notes:

* **Feeds, not a crawler.** A feed is a publisher's own declaration of what it published
  and when, which gives an accurate ``published_at`` for free — and ``published_at`` is
  load-bearing here, because temporal integrity depends on knowing when something became
  knowable. A crawler would have to guess it.
* **Quality is declared per source, not inferred.** Each feed carries a ``base_quality``
  and a ``SourceClass``, so independence scoring counts a wire service and a personal blog
  differently. The tiers come from the PRD's source table.
* **The list is configuration, not code.** ``MARKETRADAR_FEEDS`` overrides the defaults
  entirely, because "the reliable ones" is a judgement that belongs to the operator.
* **A feed that does not answer is UNAVAILABLE, never empty.** An empty result and a broken
  feed are different facts and are never conflated.

Parsing is stdlib-only (``xml.etree``). A feed library would be one more dependency to
audit for a format that is, in practice, two well-known shapes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

from marketradar.domain.enums import (
    DataMode,
    ProviderCapability,
    SourceClass,
    SourceType,
)
from marketradar.logging import get_logger
from marketradar.providers.base import (
    ProviderDocument,
    ProviderHealth,
    ProviderQuery,
    ProviderResult,
    ProviderSource,
)
from marketradar.providers.http import SafeHttpClient
from marketradar.providers.sec_text import extract_text, prose_only

log = get_logger(__name__)

FEED_PROVIDER_KEY = "rss_feeds"

#: Below this many words, a fetched article body is treated as a failed extraction (a
#: paywall stub, a cookie wall, a JS shell) and the feed's own summary is used instead.
MIN_ARTICLE_WORDS = 120

#: Feed summaries are legitimate text — a publisher's own abstract — but they are not the
#: article. Below this, there is not enough to extract a claim from and the item is dropped.
#: Deliberately low: a real RSS description is often a single sentence, and one sentence can
#: carry a whole claim ("memory contract pricing rose 15% as demand accelerated"). The
#: threshold is here to drop "Read more" stubs, not to demand an essay.
MIN_SUMMARY_WORDS = 12


@dataclass(frozen=True)
class FeedDescriptor:
    """One subscribed feed, and how much its publisher is trusted a priori."""

    key: str
    name: str
    publisher: str
    url: str
    source_type: SourceType
    source_class: SourceClass
    base_quality: int
    homepage_url: str | None = None
    #: Whether to fetch the linked article and extract its body. Off for feeds that publish
    #: full text inline, or whose articles are known to be behind a wall.
    fetch_article: bool = True
    notes: str | None = None


#: Every entry is free, public, and needs no API key — that is a hard constraint, not a
#: starting position. Within it the list is ordered by how much a claim from that publisher
#: is worth: statutory bodies publish the numbers everyone else reports on, industry press
#: sees a shift in specific language first, and general financial media mostly re-reports
#: both. The `base_quality` numbers encode exactly that and feed independence scoring.
#:
#: These endpoints CANNOT be reached from the build environment, so their URLs are
#: unverified here. That is a real risk and it is handled rather than hidden:
#: `marketradar feeds` probes every one and reports which answered, and any feed that fails
#: is UNAVAILABLE rather than quietly contributing nothing. Prune what does not work.
DEFAULT_FEEDS: tuple[FeedDescriptor, ...] = (
    # ---------------------------------------------------------------- statutory
    # Free, public, no key, and the most reliable text about the economy that exists:
    # these bodies publish the numbers everyone else reports ON. Weighted highest because
    # independence scoring should treat "the BLS said it" and "an outlet said the BLS said
    # it" as one confirmation, attributed to the BLS.
    FeedDescriptor(
        key="sec-press",
        name="SEC Press Releases",
        publisher="U.S. Securities and Exchange Commission",
        url="https://www.sec.gov/news/pressreleases.rss",
        source_type=SourceType.GOVERNMENT_DATA,
        source_class=SourceClass.REGULATORY,
        base_quality=96,
        homepage_url="https://www.sec.gov/news/pressreleases",
    ),
    FeedDescriptor(
        key="federalreserve-press",
        name="Federal Reserve Press Releases",
        publisher="Board of Governors of the Federal Reserve System",
        url="https://www.federalreserve.gov/feeds/press_all.xml",
        source_type=SourceType.GOVERNMENT_DATA,
        source_class=SourceClass.GOVERNMENT,
        base_quality=96,
        homepage_url="https://www.federalreserve.gov/newsevents.htm",
    ),
    FeedDescriptor(
        key="bls-news",
        name="BLS News Releases",
        publisher="U.S. Bureau of Labor Statistics",
        url="https://www.bls.gov/feed/bls_latest.rss",
        source_type=SourceType.GOVERNMENT_DATA,
        source_class=SourceClass.GOVERNMENT,
        base_quality=95,
        homepage_url="https://www.bls.gov/bls/newsrels.htm",
    ),
    FeedDescriptor(
        key="bea-news",
        name="BEA News Releases",
        publisher="U.S. Bureau of Economic Analysis",
        url="https://www.bea.gov/rss.xml",
        source_type=SourceType.GOVERNMENT_DATA,
        source_class=SourceClass.GOVERNMENT,
        base_quality=95,
        homepage_url="https://www.bea.gov/news/current-releases",
    ),
    FeedDescriptor(
        key="census-economic",
        name="Census Economic Indicators",
        publisher="U.S. Census Bureau",
        url="https://www.census.gov/economic-indicators/indicator.xml",
        source_type=SourceType.GOVERNMENT_DATA,
        source_class=SourceClass.GOVERNMENT,
        base_quality=94,
        homepage_url="https://www.census.gov/economic-indicators/",
    ),
    FeedDescriptor(
        key="treasury-press",
        name="U.S. Treasury Press Releases",
        publisher="U.S. Department of the Treasury",
        url="https://home.treasury.gov/rss/press.xml",
        source_type=SourceType.GOVERNMENT_DATA,
        source_class=SourceClass.GOVERNMENT,
        base_quality=94,
        homepage_url="https://home.treasury.gov/news/press-releases",
    ),
    FeedDescriptor(
        key="eia-today",
        name="EIA Today in Energy",
        publisher="U.S. Energy Information Administration",
        url="https://www.eia.gov/rss/todayinenergy.xml",
        source_type=SourceType.GOVERNMENT_DATA,
        source_class=SourceClass.GOVERNMENT,
        base_quality=93,
        homepage_url="https://www.eia.gov/todayinenergy/",
    ),
    FeedDescriptor(
        key="ecb-press",
        name="ECB Press Releases",
        publisher="European Central Bank",
        url="https://www.ecb.europa.eu/rss/press.html",
        source_type=SourceType.GOVERNMENT_DATA,
        source_class=SourceClass.GOVERNMENT,
        base_quality=93,
        homepage_url="https://www.ecb.europa.eu/press/",
    ),
    # ------------------------------------------------------ industry / technical
    # Free and public, with real editorial and domain expertise. These are where a shift
    # shows up in specific language ("lead times extended", "capacity committed") long
    # before it reaches general media — which is the whole point of watching them.
    FeedDescriptor(
        key="semiengineering",
        name="Semiconductor Engineering",
        publisher="Semiconductor Engineering",
        url="https://semiengineering.com/feed/",
        source_type=SourceType.TECHNICAL_PUBLICATION,
        source_class=SourceClass.TECHNICAL,
        base_quality=80,
        homepage_url="https://semiengineering.com/",
    ),
    FeedDescriptor(
        key="eetimes",
        name="EE Times",
        publisher="EE Times",
        url="https://www.eetimes.com/feed/",
        source_type=SourceType.SPECIALIST_PUBLICATION,
        source_class=SourceClass.INDUSTRY,
        base_quality=76,
        homepage_url="https://www.eetimes.com/",
    ),
    FeedDescriptor(
        key="ieee-spectrum",
        name="IEEE Spectrum",
        publisher="IEEE",
        url="https://spectrum.ieee.org/feeds/feed.rss",
        source_type=SourceType.TECHNICAL_PUBLICATION,
        source_class=SourceClass.TECHNICAL,
        base_quality=78,
        homepage_url="https://spectrum.ieee.org/",
    ),
    FeedDescriptor(
        key="arstechnica",
        name="Ars Technica",
        publisher="Ars Technica",
        url="https://feeds.arstechnica.com/arstechnica/technology-lab",
        source_type=SourceType.TECHNICAL_PUBLICATION,
        source_class=SourceClass.TECHNICAL,
        base_quality=72,
        homepage_url="https://arstechnica.com/",
    ),
    # ---------------------------------------------------------- financial media
    # Lowest weight on purpose. General media mostly REPORTS the sources above, and the
    # ancestry clustering exists so that ten such reports of one release count once.
    FeedDescriptor(
        key="cnbc-technology",
        name="CNBC Technology",
        publisher="CNBC",
        url="https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
        source_type=SourceType.MAJOR_FINANCIAL_MEDIA,
        source_class=SourceClass.FINANCIAL_MEDIA,
        base_quality=70,
        homepage_url="https://www.cnbc.com/technology/",
    ),
    FeedDescriptor(
        key="marketwatch-top",
        name="MarketWatch Top Stories",
        publisher="MarketWatch",
        url="https://feeds.content.dowjones.io/public/rss/mw_topstories",
        source_type=SourceType.MAJOR_FINANCIAL_MEDIA,
        source_class=SourceClass.FINANCIAL_MEDIA,
        base_quality=68,
        homepage_url="https://www.marketwatch.com/",
    ),
)

_FEED_FIELDS = 8  # key|name|publisher|url|type|class|quality|fetch_article


def parse_feed_spec(spec: str) -> FeedDescriptor:
    """Parse one ``MARKETRADAR_FEEDS`` entry.

    Pipe-separated so a URL containing a comma or an equals sign survives:
    ``key|name|publisher|url|SOURCE_TYPE|SOURCE_CLASS|quality|fetch_article``
    """
    parts = [part.strip() for part in spec.split("|")]
    if len(parts) < 7:
        raise ValueError(
            "A feed spec needs at least 7 pipe-separated fields "
            "(key|name|publisher|url|source_type|source_class|quality); got: " + spec
        )
    fetch = parts[7].lower() not in {"false", "0", "no"} if len(parts) >= _FEED_FIELDS else True
    return FeedDescriptor(
        key=parts[0],
        name=parts[1],
        publisher=parts[2],
        url=parts[3],
        source_type=SourceType(parts[4]),
        source_class=SourceClass(parts[5]),
        base_quality=int(parts[6]),
        fetch_article=fetch,
    )


# ------------------------------------------------------------------ parsing
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

#: Feeds date-stamp in RFC 822 (RSS) or RFC 3339 (Atom), with wide variation in practice.
_ISO_CLEAN = re.compile(r"(?<=[+-]\d{2})(?=\d{2}$)")


@dataclass(frozen=True)
class FeedProbe:
    """What one feed returned when it was last checked."""

    feed: FeedDescriptor
    reachable: bool
    item_count: int = 0
    newest: datetime | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        if not self.reachable:
            return f"FAILED: {self.error or 'unknown error'}"
        return "ok" if self.item_count else "reachable but empty"


@dataclass(frozen=True)
class FeedItem:
    """One entry from a feed, before an article body is fetched."""

    external_id: str
    title: str
    url: str
    summary: str
    published_at: datetime
    author: str | None = None


def parse_datetime(raw: str | None) -> datetime | None:
    """Parse a feed timestamp, returning None rather than guessing."""
    if not raw:
        return None
    text = raw.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        candidate = text.replace("Z", "+00:00")
        candidate = _ISO_CLEAN.sub(":", candidate)
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    # A naive timestamp is treated as UTC and recorded as such rather than being dropped;
    # feeds that omit an offset are common, and losing the item loses more than it saves.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(element: Any, *paths: str) -> str:
    for path in paths:
        found = element.find(path, _NS)
        if found is not None and (found.text or "").strip():
            return str(found.text).strip()
    return ""


def _link(entry: Any) -> str:
    """The article URL for an entry, in RSS and Atom alike.

    Atom puts the URL in ``<link href="...">`` with no text content, and its ``<id>`` is
    frequently a ``tag:`` URN rather than a URL. Reading the id as the link produced
    citations pointing at ``tag:journal.example,2026:slug`` — not fetchable, and not a
    citation a reader can follow. A ``rel="alternate"`` link is the entry's canonical
    document; an id is used only when it is itself a URL.
    """
    rss = _text(entry, "link")
    if rss.startswith("http"):
        return rss

    links = entry.findall("atom:link", _NS)
    for wanted in ("alternate", None):
        for anchor in links:
            rel = anchor.get("rel")
            if rel == wanted or (wanted is None and rel in (None, "")):
                href = (anchor.get("href") or "").strip()
                if href.startswith("http"):
                    return href
    for anchor in links:
        href = (anchor.get("href") or "").strip()
        if href.startswith("http"):
            return href

    identifier = _text(entry, "atom:id", "guid")
    return identifier if identifier.startswith("http") else ""


def parse_feed(xml: str) -> list[FeedItem]:
    """Parse RSS 2.0 or Atom into items, skipping entries that cannot be dated.

    An undated item cannot be placed on a timeline, and an item that cannot be placed on a
    timeline cannot participate in an as-of read without risking lookahead. Dropping it is
    the only safe option.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Feed is not well-formed XML: {exc}") from exc

    items: list[FeedItem] = []
    entries = root.findall(".//item") or root.findall(".//atom:entry", _NS)
    for entry in entries:
        title = _text(entry, "title", "atom:title")
        link = _link(entry)
        published = parse_datetime(
            _text(entry, "pubDate", "atom:published", "atom:updated", "dc:date") or None
        )
        if not link or not title or published is None:
            continue
        summary = _text(
            entry, "content:encoded", "description", "atom:content", "atom:summary"
        )
        items.append(
            FeedItem(
                external_id=_text(entry, "guid", "atom:id") or link,
                title=" ".join(title.split()),
                url=link,
                summary=prose_only(extract_text(summary)) if summary else "",
                published_at=published,
                author=_text(entry, "dc:creator", "author", "atom:author/atom:name") or None,
            )
        )
    return items


class RssFeedProvider:
    """News and blog ingestion from subscribed RSS/Atom feeds.

    One feed failing must not take the others down: each is fetched independently and a
    failure is recorded as a note on the result. The provider is available when *any*
    subscribed feed answered, and UNAVAILABLE when none did — "no articles today" and "every
    publisher is unreachable" are different facts and are never conflated.
    """

    capability = ProviderCapability.NEWS_SEARCH
    key = FEED_PROVIDER_KEY

    def __init__(
        self,
        client: SafeHttpClient,
        feeds: tuple[FeedDescriptor, ...] = DEFAULT_FEEDS,
        max_items_per_feed: int = 40,
    ) -> None:
        self._client = client
        self._feeds = feeds
        self._max_items = max_items_per_feed
        self._by_key = {feed.key: feed for feed in feeds}

    # -- descriptors ----------------------------------------------------
    def sources(self) -> tuple[ProviderSource, ...]:
        return tuple(
            ProviderSource(
                key=feed.key,
                name=feed.name,
                publisher=feed.publisher,
                source_type=feed.source_type,
                source_class=feed.source_class,
                base_quality=feed.base_quality,
                homepage_url=feed.homepage_url,
                is_synthetic=False,
                data_mode=DataMode.LIVE,
                notes=feed.notes,
            )
            for feed in self._feeds
        )

    # -- health ---------------------------------------------------------
    def health(self) -> ProviderHealth:
        if not self._feeds:
            return ProviderHealth(
                provider_key=self.key,
                capability=self.capability,
                available=False,
                mode=DataMode.UNAVAILABLE,
                detail=(
                    "No feeds are subscribed. Set MARKETRADAR_FEEDS, or leave it unset to "
                    "use the built-in list of reliable publishers."
                ),
                checked_at=datetime.now(tz=UTC),
            )
        return ProviderHealth(
            provider_key=self.key,
            capability=self.capability,
            available=True,
            mode=DataMode.LIVE,
            detail=f"{len(self._feeds)} subscribed feeds.",
            checked_at=datetime.now(tz=UTC),
        )

    # -- retrieval ------------------------------------------------------
    def get_recent_documents(
        self, limit: int = 200, until: datetime | None = None
    ) -> ProviderResult:
        """Fetch every subscribed feed, newest first, windowed at ``until``.

        ``until`` is the as-of instant. Windowing happens HERE rather than downstream so a
        historical replay never even retrieves an article published after the moment being
        replayed (the lookahead fix, ADR-013).
        """
        documents: list[ProviderDocument] = []
        notes: list[str] = []
        answered = 0

        for feed in self._feeds:
            try:
                items = self._fetch_feed(feed)
            except Exception as exc:  # noqa: BLE001 - one bad feed must not stop the rest
                log.warning("feeds.fetch_failed", feed=feed.key, error=str(exc))
                notes.append(f"{feed.key}: {exc}")
                continue
            answered += 1
            for item in items[: self._max_items]:
                if until is not None and item.published_at > until:
                    continue
                document = self._to_document(feed, item)
                if document is not None:
                    documents.append(document)

        if answered == 0:
            return ProviderResult(
                provider_key=self.key,
                mode=DataMode.UNAVAILABLE,
                query="",
                documents=(),
                detail=(
                    "No subscribed feed could be reached: "
                    + ("; ".join(notes) if notes else "no feeds subscribed")
                ),
            )

        documents.sort(key=lambda d: d.published_at, reverse=True)
        return ProviderResult(
            provider_key=self.key,
            mode=DataMode.LIVE,
            query="",
            documents=tuple(documents[:limit]),
            truncated=len(documents) > limit,
            detail=(
                f"{answered}/{len(self._feeds)} feeds answered."
                + (f" Failures: {'; '.join(notes)}" if notes else "")
            ),
        )

    def search(self, query: ProviderQuery) -> ProviderResult:
        """Keyword filter over recently published items.

        Feeds have no server-side search, so this is a client-side filter over what the
        publishers have recently declared. That is a real limitation and is stated in the
        result detail rather than presented as a search of the whole web.
        """
        result = self.get_recent_documents(limit=500, until=query.until)
        if result.mode == DataMode.UNAVAILABLE:
            return result
        terms = {term.lower() for term in (query.terms or ())} | (
            {query.text.lower()} if query.text else set()
        )
        terms.discard("")
        if not terms:
            matched = result.documents
        else:
            matched = tuple(
                document
                for document in result.documents
                if any(
                    term in document.title.lower() or term in document.body_text.lower()
                    for term in terms
                )
            )
        return ProviderResult(
            provider_key=self.key,
            mode=DataMode.LIVE,
            query=query.text,
            documents=matched[: query.limit],
            truncated=len(matched) > query.limit,
            detail=(
                "Client-side keyword filter over recently published feed items; "
                "feeds expose no server-side search, so this is not a search of the web."
            ),
        )

    def probe(self) -> list[FeedProbe]:
        """Check every subscribed feed and report what it returned.

        Feed URLs rot — publishers move endpoints, drop RSS, or put a wall in front of one.
        A dead feed contributes nothing and, without a probe, contributes nothing *silently*,
        which is indistinguishable from a quiet news day. Fetching the feed only, never the
        articles, so this stays cheap enough to run whenever something looks wrong.
        """
        results: list[FeedProbe] = []
        for feed in self._feeds:
            try:
                items = self._fetch_feed(feed)
            except Exception as exc:  # noqa: BLE001 - reporting the failure IS the job
                results.append(FeedProbe(feed=feed, reachable=False, error=str(exc)))
                continue
            results.append(
                FeedProbe(
                    feed=feed,
                    reachable=True,
                    item_count=len(items),
                    newest=max((item.published_at for item in items), default=None),
                )
            )
        return results

    def get_document(self, external_id: str) -> ProviderDocument | None:
        for document in self.get_recent_documents(limit=1000).documents:
            if document.external_id == external_id:
                return document
        return None

    # -- internals ------------------------------------------------------
    def _fetch_feed(self, feed: FeedDescriptor) -> list[FeedItem]:
        return parse_feed(self._client.get_text(feed.url))

    def _to_document(
        self, feed: FeedDescriptor, item: FeedItem
    ) -> ProviderDocument | None:
        """Build a document, preferring the article body over the feed's own summary."""
        body = ""
        body_source = "summary"
        if feed.fetch_article:
            try:
                article = prose_only(extract_text(self._client.get_text(item.url)))
            except Exception as exc:  # noqa: BLE001 - a wall or a 404 is not fatal
                log.info("feeds.article_unavailable", url=item.url, error=str(exc))
                article = ""
            if len(article.split()) >= MIN_ARTICLE_WORDS:
                body, body_source = article, "article"

        if not body:
            # The publisher's own abstract is real text, not a fabrication — but it is
            # recorded as an abstract so nothing downstream mistakes it for the article.
            if len(item.summary.split()) < MIN_SUMMARY_WORDS:
                log.info("feeds.item_too_short", url=item.url)
                return None
            body = item.summary

        return ProviderDocument(
            external_id=f"feed:{feed.key}:{item.external_id}",
            source_key=feed.key,
            url=item.url,
            title=item.title,
            body_text=body,
            published_at=item.published_at,
            # A feed states when an item was PUBLISHED. When the described event occurred is
            # not stated, so it is left None and ingestion records the inference (ADR-013).
            event_at=None,
            author=item.author,
            data_mode=DataMode.LIVE,
            payload={
                "feed": feed.key,
                "publisher": feed.publisher,
                # Whether the stored text is the article or the publisher's abstract. An
                # abstract supports weaker claims, and a reader must be able to tell.
                "body_source": body_source,
            },
        )


__all__ = [
    "DEFAULT_FEEDS",
    "FEED_PROVIDER_KEY",
    "MIN_ARTICLE_WORDS",
    "MIN_SUMMARY_WORDS",
    "FeedDescriptor",
    "FeedItem",
    "FeedProbe",
    "parse_datetime",
    "parse_feed",
    "parse_feed_spec",
    "RssFeedProvider",
]
