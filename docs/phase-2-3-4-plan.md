# Phase 2 — Real Data

**Status:** in progress. This document is the plan the Phase 2+3+4 brief required before
implementation, written after establishing the real repository state rather than from the
brief's summary of it.

---

## 1. Actual current state (verified, not assumed)

Verified by inspection and execution on `main` @ `7efb61b` plus the temporal fix:

| Claim in the brief | Reality |
| --- | --- |
| "Phase 2+3+4 implementation completed" | **False.** No such commits, branches or files exist. |
| 145 tests passing | True — now 151 after the temporal fix. |
| Provenance / ancestry model | True and genuinely working (52 evidence → 11 independent). |
| Deterministic signal + trend engines | True. Change detection against baselines works. |
| Knowledge graph + value-chain traversal | Traversal works; **all 20 edges hand-entered, 0 with evidence**. |
| Real providers | **None.** Every row in the database is `DEMO`. |
| Entity resolution | **No module exists.** Naive substring matching misattributes. |

Fixed before starting this phase (Cycle 3 brief, Step 0):

- **Lookahead bias** — `Event.knowable_at` added; every as-of read gated on it; ingestion
  windowed at the provider. Future documents ingested went 15 → 0, contaminated events
  29 → 0. Six regression tests.
- **`detected_at`** now records the as-of instant, making discovery lead time computable
  from a replay.

## 2. Environment constraint (governs how this is verified)

`sec.gov`, `data.sec.gov` and `efts.sec.gov` are **blocked by this environment's network
policy** — the egress proxy answers `403` to `CONNECT`, recorded as `connect_rejected` in
its own status endpoint. This is an organisation policy denial, not a transient failure,
and the proxy documentation states such denials must be reported rather than retried.

Consequence, stated plainly: **the live HTTP path cannot be executed here.** The strategy
is therefore:

1. Build the real providers with transport injected, so every layer *except* the socket is
   executable and tested here.
2. Test parsing exhaustively against realistic recorded-shape payloads, including the
   malformed and partial cases.
3. Have the operator run live ingestion on a machine with unrestricted network, and verify
   from its output.
4. Until step 3 passes, the provider's `health()` reports `UNAVAILABLE` with
   `live_path_verified=false`, and the UI shows it. Nothing claims to be live that has not
   been observed to be live.

## 3. Provider strategy

Ordered by information value per unit of cost, not by ease of integration.

### 3.1 SEC company data (free, no key)

`https://www.sec.gov/files/company_tickers.json` → every US registrant's CIK, ticker and
name. Populates real `Company` rows with `data_mode = LIVE` and `is_fictional = false`.
This is the reference universe entity resolution needs.

### 3.2 SEC filings — metadata *and document text* (free, no key)

`https://data.sec.gov/submissions/CIK##########.json` gives the filing index. The existing
adapter stops there and synthesises a one-line body, which yields no extractable evidence —
that is why it must go further:

`https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{primary_document}` is the actual
filing. Its text is the highest-quality source class in our own scoring model: management
language, guidance changes, capacity commentary, demand descriptions.

Requirements:
- Declarative `User-Agent` with contact details (SEC fair-access policy; requests without
  one are refused).
- **≤ 10 requests/second**, enforced client-side.
- HTML/XBRL stripped to text before extraction.
- Filing date and report period kept **distinct** — see §4.

### 3.3 Market data — still unavailable

No vendor is configured. Market-derived components continue to report `UNAVAILABLE` and be
excluded from scores with renormalisation. This is unchanged and remains honest.

## 4. Temporal architecture for real filings

The single largest correctness risk when real filings arrive is confusing three dates that
EDGAR exposes together:

| Date | EDGAR field | Meaning | Used as |
| --- | --- | --- | --- |
| Filing date | `filingDate` | when the document became public | `published_at`, and therefore `knowable_at` |
| Report period | `reportDate` | the period the filing describes | `event_at` |
| Acceptance | `acceptanceDateTime` | timestamp of acceptance | tiebreak for same-day filings |

A 10-Q filed 2026-05-01 for the quarter ended 2026-03-31 **occurred** in March and became
**knowable** on 1 May. Treating `reportDate` as publication would back-date knowability by
weeks and reintroduce exactly the lookahead the previous fix removed. Tests assert this
specific case.

## 5. Entity-resolution strategy

Replaces the current `if surface.lower() in lowered` substring match, which attributes
evidence containing the word "all" to Allstate.

Design:
- **Word-boundary matching only** — regex alternation compiled from the surface forms.
- **Surface forms per company**: legal name, common name, name with suffixes stripped
  (Inc./Corp./plc/Holdings), known aliases and former names, ticker.
- **Ticker matching is context-gated.** A bare ticker is accepted only when it appears
  cased as a ticker (uppercase) or adjacent to a ticker cue (`NASDAQ:`, `(TICKER)`), because
  ordinary English words are also tickers (`ON`, `ALL`, `KEY`, `IT`, `CAT`).
- **Ambiguity is preserved, not resolved by luck** — a surface matching several companies
  yields multiple candidates with confidences; first-match-wins is removed.
- **Share classes collapse** to one issuer (GOOG/GOOGL → Alphabet).
- Every resolution carries a confidence and the surface form that produced it, so a
  mis-mapping is inspectable rather than invisible.

Measured, not asserted: a false-attribution rate on a labelled sample is part of the
acceptance criteria.

## 6. Implementation sequence

1. Plan (this document) — **done**
2. SEC company provider → real `Company` universe
3. SEC filings provider → submissions **+ document text**, rate limited
4. Entity resolution service, replacing substring matching
5. Pipeline wiring, `LIVE` mode propagation, graceful degradation
6. Live verification on an unrestricted machine — **done, see §11**
7. Value-chain relationships extracted from filings with evidence (audit C5) — **done**
8. Subject discovery, replacing the hardcoded three-subject vocabulary (audit C6) — open

## 7. Testing strategy

- Parsing: exhaustive fixtures per endpoint, including truncated, malformed and
  empty payloads.
- Temporal: filing date vs report period, asserted explicitly.
- Rate limiting: asserted by a fake clock, not by sleeping.
- Entity resolution: the audit's own false-positive cases (`on`, `all`, `key`) must resolve
  to nothing; share classes must collapse; ambiguous surfaces must return multiple
  candidates.
- Degradation: every provider failure path must produce `UNAVAILABLE`, never a silent empty.

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Live path unverifiable here | Verified by the operator on an unrestricted machine before any `LIVE` claim |
| SEC rate limiting / blocking | ≤10 rps enforced client-side, declarative User-Agent, backoff |
| Filing text is large and noisy | Size caps, section targeting, text extraction tested on real-shaped HTML |
| Extractor tuned on synthetic prose | Recall on real filings is unknown and must be measured — an accepted open risk |
| Entity resolution false positives on ~10k tickers | Context-gated tickers, measured false-attribution rate |

## 9. Acceptance criteria

Phase 2 is complete only when **all** hold:

- Real companies ingested with `data_mode = LIVE` and `is_fictional = false`
- Real filing **text** ingested, with filing date and report period stored distinctly
- No document published after the as-of instant enters an as-of run (already enforced)
- Entity resolution produces zero attributions for the audit's false-positive cases, with a
  measured false-attribution rate on a labelled sample
- At least one signal computed from real evidence about a real company — **open**: the live
  path is verified end to end, but a full `pipeline` run against SEC has not yet been observed
- The live path executed and observed on a real network, with output inspected — **done (§11)**
- `health()` reports `LIVE` only after that observation — **done**


---

## 10. Running the live path

The live path must be executed somewhere with unrestricted network access. On such a
machine:

```bash
export MARKETRADAR_COMPANY_PROVIDER=sec
export MARKETRADAR_FILINGS_PROVIDER=sec_edgar
# SEC refuses requests without a descriptive User-Agent carrying real contact details.
export MARKETRADAR_SEC_USER_AGENT="Market Radar your@email.com"
# Watchlist of CIKs whose filings are ingested (see the limitation below).
export MARKETRADAR_SEC_CIKS=320193,789019,1045810

make -C backend live-check     # or: python -m marketradar.cli live-check
```

`live-check` performs the smallest real request each provider supports and prints what
came back, exiting non-zero if a configured real provider did not answer. It turns "the
live path works" from an expectation into an observation.

Then:

```bash
python -m marketradar.cli sync-companies   # real issuers, data_mode=LIVE
python -m marketradar.cli pipeline         # real filings -> evidence -> graph -> signals
python -m marketradar.cli graph            # the edges, and the sentence each one rests on
python -m marketradar.cli show <theme-slug>
```

### In Docker

Use the `cli` service, never `api`, for one-off commands. `api` depends on `bootstrap`
completing, so a failed demo bring-up blocks operator commands that have nothing to do with
it; `cli` depends only on the database and publishes no host ports, so it cannot collide
with a second stack either.

```bash
# If another stack (or a local Postgres) already holds these ports:
export MARKETRADAR_POSTGRES_HOST_PORT=5433
export MARKETRADAR_API_HOST_PORT=8001
export MARKETRADAR_WEB_HOST_PORT=3001

docker compose up -d postgres
docker compose --profile cli run --rm cli alembic upgrade head
docker compose --profile cli run --rm \
  -e MARKETRADAR_COMPANY_PROVIDER=sec \
  -e MARKETRADAR_FILINGS_PROVIDER=sec_edgar \
  -e MARKETRADAR_SEC_USER_AGENT="Market Radar your@email.com" \
  -e MARKETRADAR_SEC_CIKS=320193,789019,1045810 \
  cli python -m marketradar.cli pipeline
docker compose --profile cli run --rm cli python -m marketradar.cli graph --limit 40
```

### Stated limitation: watchlist, not discovery

`MARKETRADAR_SEC_CIKS` is a **watchlist**. The system monitors the issuers named there; it
does not yet scan the whole registrant universe. That is a deliberate scoping decision for
this phase, not an oversight — the full-universe crawl is a rate-limit and storage problem
rather than an intelligence one — but it means the current system performs *trend
monitoring over a chosen universe*, and calling it discovery would overstate it.

### Known performance characteristics

Measured on a synthetic 10,000-issuer universe: resolver construction 0.66 s, resolution
~150 ms per 2,400-word document. Ingesting 1,000 filings therefore spends roughly 2.5
minutes in entity resolution alone. Acceptable now, and the obvious first target if
throughput becomes a constraint.


---

## 11. Live verification result (observed 2026-09-08)

Run by the operator on macOS, in the API container, against the real SEC endpoints. Reported
verbatim rather than summarised, because this is the observation that permits any `LIVE` claim:

```
ok NEWS_SEARCH   DEMO          verified=True  Synthetic development corpus v1.1.0.
ok FILINGS       LIVE          verified=True  SEC EDGAR submissions API reachable.
!! MARKET_DATA   UNAVAILABLE   verified=True  No market-data provider is configured.
ok COMPANY_DATA  LIVE          verified=True  SEC company file reachable; 8010 registrants.

Company universe: 8010 issuers
  NVDA  NVIDIA CORP      cik=0001045810 mode=LIVE
  AAPL  Apple Inc.       cik=0000320193 mode=LIVE
  ...
Filings fetched: 3 (mode LIVE)
  2026-09-03 NVIDIA CORP — 8-K      910 words   event_at=2026-09-02
  2026-09-02 MICROSOFT CORP — 8-K   508 words   event_at=2026-09-02
  2026-08-26 NVIDIA CORP — 10-Q   16252 words   event_at=2026-07-26
```

What this establishes:

- **The company universe is real.** 8,010 exchange-listed registrants from
  `company_tickers_exchange.json`, keyed by CIK, share classes collapsed.
- **Filing text is real and fetched, not stubbed.** 910 / 508 / 16,252 words. A filing whose
  body cannot be retrieved is dropped rather than stored as a metadata stub.
- **The temporal split is real.** The 10-Q was *filed* 2026-08-26 for a period ending
  2026-07-26. `published_at` and `event_at` differ by 31 days on a real document, which is
  precisely the gap that fabricates urgency when the two are collapsed.
- **Rate limiting and the SSRF allowlist held** across the eight requests shown.
- **Market data remains honestly `UNAVAILABLE`.** Nothing was invented to fill it.

What this does **not** establish: a full `pipeline` run against SEC (evidence, events and
signals from real filings) has not yet been observed, and the extractor's recall on real
filing prose is still unmeasured. Both are named in §8 as accepted open risks.


---

## 12. First full pipeline run on real filings (observed 2026-09-09)

72 real SEC documents over AAPL / MSFT / NVDA. This is the run that matters, because it is
the first time the extraction path met filing prose it was not written against. It found
four defects that no test in the suite had caught, every one of them now pinned.

```
Documents:  seen=72 created=72     Evidence: 162     Events: 85
Graph:      2 edges created (6 relationship citations), without_filer=24
Themes:     memory-change          Exposures: 0
```

### D1 — A confident, entirely fictitious customer edge (CRITICAL)

```
sec-0000832428 --[BUYS_FROM w=0.60 c=0.54 LIVE]--> sec-0000789019
  "We use a range of amounts to estimate SSP when we sell each of the products and
   services separately and need to determine whether there is a discount to be allocated"
```

Microsoft revenue-recognition boilerplate, read as a customer relationship. Two independent
defects had to line up, and each is fixed separately so either alone would have stopped it:

* **The resolver matched bare lower-case English words to issuers.** The SEC universe
  contains registrants named *Various*, *Discount*, *Range*, *Match*, *Block*. Matched
  case-insensitively, they fire on ordinary prose — here at **0.92 confidence**. This is the
  C4 substring failure resurfacing on a vocabulary the fix's word list did not contain, and
  a hand-maintained list was never going to be the answer at 8,010 issuers. A one-word name
  surface now requires the occurrence to be **capitalised**; sentence-initial capitalisation
  carries no information, so it is accepted at reduced confidence rather than trusted.
* **A rule keyed on an infinitive marker.** `we sell … to` matched the "to" in "need **to**
  determine". Cues ending on an infinitive-taking verb are now rejected.

### D2 — Two of three disclosed suppliers silently dropped (HIGH)

```
"We purchase memory from SK Hynix Inc., Micron Technology, Inc., and Samsung."
```

produced exactly **one** edge. Every rule's party window is bounded by `[^.;]`, which stops
dead at the period in "Inc." — so only the first supplier in any enumeration was ever seen.
Abbreviation periods are now neutralised before matching by a **length-preserving**
substitution, so every offset still indexes the original document and excerpts stay exact.
The sentence now yields SK Hynix *and* Micron; Samsung Electronics is not an SEC registrant,
so it correctly yields nothing rather than a dangling edge.

### D3 — A real theme that mapped to zero companies (HIGH)

`memory-change` formed from real evidence and reached **no** companies. Theme anchors were
concept nodes only (`memory_requirement`, `hbm`), and a graph read out of real filings is
almost entirely company-to-company: filings say who supplies whom, and rarely say "we
produce high-bandwidth memory" in the vocabulary a concept lexicon happens to know. The
traversal started at entities the graph did not contain.

Two changes, both of which real data requires:

* **Evidence anchors.** The companies a theme's own evidence names are now first-order
  anchors — they are the entry point that is always available, and the extracted edges
  extend outward from there. Anchor weight saturates in the number of independent evidence
  clusters, so one report does not anchor a theme as hard as five.
* **Filings are about their filer.** "Demand for our products increased" names no company,
  but it is unambiguously a claim about the company that filed it. Evidence from a filing
  now falls back to the filer when the sentence itself names nobody — used only as a
  fallback, so an explicit mention of a different issuer always wins. On the demo corpus
  this took company-attributed events from **3 to 26**.

### D4 — The graph was unauditable (MEDIUM)

`marketradar graph` printed company keys, which for real issuers are zero-padded CIKs. Both
edges above were unreadable without looking up two CIKs by hand — which is precisely why D1
went unnoticed on first reading. It now prints `NAME [key]`.

### What this run says about coverage

2 edges from 72 documents, of which 1 was wrong. The corrected extractor would have produced
3 correct edges and 0 wrong ones from the same corpus. That is a small number, and honestly
so: 8-Ks rarely carry supply-chain language, and held-out recall on unseen filing phrasings
is 0.29. The measurement stands as the argument for an LLM extractor behind the same
interface — now with a real-filing test set (`FIELD` in
`tests/unit/test_relationship_extraction.py`) for it to beat.

### Still open

* `without_filer=24` — the DEMO news provider is still active alongside the live SEC one, so
  the run mixed corpora. Every document is labelled with its own `data_mode` and the theme
  takes the weakest, so nothing is mislabelled, but a pure-LIVE run needs the news provider
  disabled.
* Recall on real filings is measured on two sentences. That is a start, not a sample.

---

## 13. The second run, and the defect it exposed (2026-09-09)

Re-running the fixed pipeline against the database from §12 reported:

```
Documents:  seen=72 created=0 skipped=72
Evidence:   0 created      Events: 0 created      Graph: 0 edges created
```

— and `marketradar graph` still showed the fabricated edge, now legibly:

```
E.W. SCRIPPS Co [sec-0000832428] --[BUYS_FROM]--> MICROSOFT CORP [sec-0000789019]
SK hynix Inc.   [sec-0002120882] --[SUPPLIES]--> NVIDIA CORP     [sec-0001045810]
```

The D4 naming fix did its job: a broadcaster as a customer of Microsoft is obviously wrong,
where two bare CIKs were not. But **none of the D1–D3 fixes reached the data.**

**D5 — an extractor improvement cannot reach an existing database (CRITICAL).** Extraction
is idempotent on `(document, span, rule, extractor version)`. The rules were fixed without
bumping the version, so every document looked already-done, nothing re-ran, and the
fabricated edge survived a pipeline run specifically intended to remove it. A version bump
alone would not have been enough either — superseded rows do not delete themselves, so the
corpus would carry two generations of evidence and double-count them into events.

Fixed by making a version bump mean what it should: **retract, then re-extract** (ADR-017).
`ingestion/retraction.py` withdraws the previous version's evidence, events, extracted edges
and any finding left with no support. Documents are never touched — they are the expensive,
rate-limited, non-derived part. Curated edges are never deleted; migration 0003 adds
`entity_relationships.extractor_version` so "derived" is recorded rather than guessed at.

Verified against a database stamped at the old versions: 92 evidence, 42 events and 20 edges
retracted, then re-extracted from the same 38 documents with no re-fetching.

Both extractor versions are now `1.1.0` / `rel-1.1.0`, so the next run on any existing
database will retract and re-extract automatically.


---

## 14. First discovery run on real feeds (observed 2026-09-09)

Real RSS ingestion plus the SEC universe, then `subjects` and `trending`. Both outputs were
wrong, in ways only real data could have shown.

### D6 — A copper miner ranked as a top AI-memory stock (CRITICAL)

```
4  HBM   Hudbay Minerals Inc.   6.2  tailwind   AI Memory Demand
1  CTS   CTS CORP               6.6  tailwind   AI Memory Demand
2  BAND  Bandwidth Inc.         6.5  tailwind   AI Memory Demand
```

`HBM` is Hudbay Minerals' ticker **and** high-bandwidth memory. `CTS`, `BAND` and `SKHY`
arrived the same way. The C4 ticker gate required an uncued ticker to be upper-case and at
least three characters — but technical prose is full of upper-case acronyms, so the gate was
letting through exactly the collisions it was built to stop.

An uncued ticker now requires **the issuer's name to appear in the same text**. A document
genuinely about Micron says "Micron" somewhere; one about high-bandwidth memory does not say
"Hudbay Minerals". The cost is a ticker-only mention; the gain is removing a whole class of
confident, absurd attribution. An explicitly cued ticker (`(HBM)`, `NYSE: HBM`) still
resolves alone — a cue *is* the corroboration.

### D7 — Website furniture presented as discovered subjects (HIGH)

```
may earn compensation · affiliate link policy · california privacy right
reproduced distributed transmitte · around the web · site constitute acceptance
```

Cookie notices, affiliate disclosures and copyright footers. Two causes, both fixed:

* **Article extraction swallowed page chrome.** `extract_text` was written for SEC filings,
  which have no navigation or affiliate blocks. It now drops `nav`, `footer`, `aside`,
  `form`, and containers whose class or id marks them as chrome.
* **The corpus-wide frequency ceiling cannot see furniture.** One publisher's footer is on
  every page it publishes and on nobody else's, so it never approaches a corpus-wide
  threshold. Saturation is now measured **per source**.

The instructive part is the first attempt at that second fix: rejecting any term that
saturates one publisher also rejected "grid storage" from a corpus entirely about grid
storage. A trade journal's *beat* saturates its source exactly as its footer does.
Furniture is a **contrast between** sources — a term that saturates one publisher and is
essentially absent from the others — and with a single source there is no contrast to
measure, so nothing is called furniture.

### Still open

The ratings above read `DEMO` because the synthetic corpus was ingested alongside the live
one. Every row is labelled correctly and nothing is mislabelled, but a pure-LIVE run needs
the fixture providers switched off.

---

## 15. The screen the product is for (2026-09-10)

Until now the answer to "where do I see the trending stocks?" was a CLI command. The rating
existed, the reasoning behind it existed, and neither was reachable from the browser — the
web UI showed themes only. That is the wrong shape for a product whose stated purpose is to
*present* trending stocks with a rating.

**What was added.**

* `GET /trending` — companies ranked 0-10, each row carrying its full score decomposition,
  its weight coverage and the list of components that could not be computed.
* `GET /subjects` — what the corpus turned out to be about, with the independent-cluster
  count that qualified each term and the `is_discovered` flag that separates a topic the
  system found from one the lexicon declared.
* `/trending` in the web UI — the ranked list, each row expanding into the components that
  produced it, the position on the value chain, the independent cluster count, and a link
  into the theme behind it. Below it, the discovered subjects as chips carrying their
  cluster counts.
* The dashboard now leads with the top eight names; the header carries a nav.

**Two decisions are recorded as ADRs rather than left in the code.** The endpoint recomputes
on read instead of serving stored `Score` rows (ADR-023) — this project has twice shipped a
fix that never reached what the reader saw, and a trending list is the worst possible place
to serve a stale number. And headwind companies are shown and labelled rather than filtered
(ADR-024): that a rising theme is bad news for a named company is often the more actionable
half of the observation.

**The expandable row is plain `<details>`.** No client component, no JavaScript. The page is
a server component that renders the reasoning inline; the browser does the toggling. This is
not minimalism for its own sake — every number on the page comes from the response that
rendered it, so there is no second code path that could disagree with the first about what
the rating is.

### Still open

Unchanged from §14: ratings read `DEMO` while the synthetic corpus shares the database with
the live one. `make reset` before a live run is the workaround; the README now says so under
*Running against real sources*. Nothing is mislabelled — weakest-wins is doing exactly what
it should — but a purely live run has not yet been observed end to end.

---

## 16. "I don't see any value" (2026-09-10)

The operator's verdict on the screen shipped in §15, verbatim: *"i don't understand anything
from what I see on the UI. i simply need to see the coolest stocks to invest in... read the
latest news, latest blogs and so on... i dont see any value now."*

That is a correct verdict, and three separate mistakes produced it.

**The default was the fake corpus.** `news_provider` defaulted to `fixture`, so a first run
ranked invented companies — Northbridge Memory Corp, ticker NBMX. There is no value in a
ranked list of things that do not exist, and the `DEMO` badge does not fix it: a reader
scanning for stocks reads tickers, not provenance chrome. Defaults are now the real providers
(ADR-025) and fictional issuers are excluded from the ranked list outright (ADR-026).

**The page never showed a headline.** The product ingests sixteen publishers, extracts claims
from articles, and clusters them by ancestry — and the UI displayed none of it. It showed
`Independent corroboration 75/100 × 16% weight = 11.8 pts`. The reader asked for news and
blogs; they got the arithmetic performed on news and blogs. Each row now leads with the
articles behind it — title, publisher, how long ago, link — and the arithmetic is one
collapsed line.

**The language was internal.** "Direct Beneficiary of 'AI Memory Demand' at order 1" and
"AI Memory Demand is accelerating, and this company is at the centre of it" are the same
fact; only the second is a reason to keep reading.

### The mistake inside the fix

The plain-language sentence originally read "5 publishers have reported on it". It was built
from the publisher count, which is precisely the number this system exists not to trust — five
outlets running one wire story is one source. Worse, the first correction reached for
`rating.independent_clusters`, which counts the *theme's* corroboration, not the company's: it
can exceed the company's own publisher count, and a company known from a single article was
described as corroborated by seven sources.

Both are now separate fields, and the sentence states the relationship rather than picking a
side: *"2 sources reported it independently of each other, across 5 publishers — the rest are
running the same story."* That sentence is the most valuable thing on the page. It is the one
claim a news feed cannot make.

### The other thing this broke

Flipping the provider defaults made the test suite reach for the network — `create_app()`
builds its registry from process-wide settings, so `/providers` in an API test tried to fetch
sixteen live feeds and the suite hung. Providers are now pinned to the synthetic corpus in
`conftest` at the environment level, before any `Settings` is constructed. A suite whose
result depends on whether a publisher answered today is not a test.

### Not verified here

This container's egress policy denies all sixteen feed hosts and `sec.gov` (403 on CONNECT),
so the live path could not be exercised in this session — the layout above was checked against
the synthetic corpus via `?demo=1`. The live run has to happen on the operator's machine,
where 11-14 of the 16 feeds answered previously.
