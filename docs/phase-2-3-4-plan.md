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
6. Live verification on an unrestricted machine
7. Value-chain relationships extracted from filings with evidence (audit C5)

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
- At least one signal computed from real evidence about a real company
- The live path executed and observed on a real network, with output inspected
- `health()` reports `LIVE` only after that observation


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
python -m marketradar.cli pipeline         # real filings -> evidence -> signals
python -m marketradar.cli show <theme-slug>
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
