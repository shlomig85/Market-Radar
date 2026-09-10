# Data sources

Every source Market Radar reads is **free and public**. Nothing here needs a paid API key, a
trial, or a scraped login. That constraint was set deliberately, and it costs something worth
stating plainly: there is no professional newsfeed, no analyst estimate, no fundamentals API
and no price data behind any figure this system produces. Where those would be needed, the
answer is `UNAVAILABLE` rather than a guess.

## Reliability, and how it is enforced

A free corpus is only worth reading if the unreliable parts of it cannot quietly become
evidence. Four mechanisms do that work, and none of them depends on trusting a publisher:

* **Source quality is a property of the publisher, not of the story.** A statutory filing
  outranks a press release, which outranks trade press, which outranks financial media.
  The weight is fixed in the feed table below and cannot be raised by how emphatic an
  article is.
* **Corroboration is counted in ancestry clusters, not documents.** Fourteen outlets running
  the same wire story are one confirmation. This is what stops a syndicated press release
  from looking like consensus.
* **A subject must be supported by several independent clusters** before it exists at all,
  so one publisher cannot mint a topic by writing about it repeatedly.
* **Retrieved pages are data, never instructions.** Article text is parsed and matched
  against extraction rules. It is never executed, never given to a tool, and never allowed
  to change what the pipeline does.

## The feeds

Fourteen RSS/Atom feeds, weighted by what kind of source they are. `base_quality` is the
publisher's floor; individual documents can score lower, never higher.

| Key | Publisher | Class | Quality |
| --- | --- | --- | --- |
| `sec-press` | U.S. Securities and Exchange Commission | Regulatory | 96 |
| `federalreserve-press` | Board of Governors of the Federal Reserve System | Government | 96 |
| `federalreserve-monetary` | Board of Governors of the Federal Reserve System | Government | 96 |
| `sec-litigation` | U.S. Securities and Exchange Commission | Regulatory | 94 |
| `bls-news` | U.S. Bureau of Labor Statistics | Government | 95 |
| `bea-news` | U.S. Bureau of Economic Analysis | Government | 95 |
| `census-economic` | U.S. Census Bureau | Government | 94 |
| `treasury-press` | U.S. Department of the Treasury | Government | 94 |
| `eia-today` | U.S. Energy Information Administration | Government | 93 |
| `ecb-press` | European Central Bank | Government | 93 |
| `semiengineering` | Semiconductor Engineering | Technical | 80 |
| `eetimes` | EE Times | Industry | 76 |
| `ieee-spectrum` | IEEE | Technical | 78 |
| `arstechnica` | Ars Technica | Technical | 72 |
| `cnbc-technology` | CNBC | Financial Media | 70 |
| `marketwatch-top` | MarketWatch | Financial Media | 68 |

Check which of them your network can actually reach:

```bash
make feeds
```

Feeds go down, move, and return 403 to unfamiliar clients. A feed that fails is reported as
failing and its documents are simply absent — the run continues, and no other source is
scaled up to compensate for the gap.

## SEC EDGAR

Two live paths, both on the free public API:

* **Company tickers** (`https://www.sec.gov/files/company_tickers.json`) — the CIK ⇄ ticker
  map, roughly 8,000 registrants. This is the only thing that turns a name in an article
  into a security.
* **Filings** (`https://data.sec.gov/submissions/CIK##########.json`, then the document
  itself) — the text of 10-K, 10-Q and 8-K filings, which is where value-chain relationships
  are extracted from.

SEC requires a descriptive `User-Agent` with a contact address and rate-limits to 10
requests/second. Both are enforced in `providers/http.py`; a run without
`MARKETRADAR_SEC_USER_AGENT` set is refused rather than sent anonymously.

## What is NOT a source

* **No market data.** No price, volume, market cap or valuation input exists. The
  `price_confirmation` component of every company rating is therefore `UNAVAILABLE` and its
  weight is redistributed across the components that were computed.
* **No analyst estimates, no fundamentals API, no alternative data.**
* **No social media.** Reddit, X and StockTwits are free and public, and they are excluded on
  purpose: the independence machinery cannot distinguish a thousand accounts repeating a
  claim from a thousand people observing it.

## Network egress

Outbound HTTP is restricted to an allowlist of hosts, extended at runtime only with hosts a
configured feed actually declares. Redirects are followed to a bounded depth and re-checked
against the allowlist at every hop, and any host resolving to a private, loopback,
link-local or reserved address is refused. TLS verification is never disabled.
