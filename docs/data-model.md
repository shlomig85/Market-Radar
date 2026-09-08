# Data model

27 tables in one PostgreSQL schema, grouped by concern. Nothing is collapsed into a generic
"item" table: an event, a signal observation and a finding are different things with
different lifecycles, and the schema says so.

Conventions that apply everywhere:

| Convention | Why |
| --- | --- |
| `data_mode` on every row carrying outside information | Provenance is a column, not a comment. It propagates downstream, weakest-wins. |
| Three separate timestamps on documents | `published_at` ≠ `event_at` ≠ `retrieved_at` (ADR-013). Recency uses `event_at`. |
| `*_version` on every computed row | Scores, signals, trends, exposures and extractions record the code version that produced them, so a historical figure stays reproducible. |
| JSONB only where the shape is genuinely open | Provider payloads, agent envelopes, score input bags, graph paths. Anything queried is a real column with an index. |
| Natural dedupe keys | `content_hash`, `dedupe_key`, `cluster_key` make every pipeline stage idempotent by construction rather than by convention. |

---

## 1. Provenance

```
sources ──< source_documents >── evidence_clusters
                  │
                  └──< evidence_items
```

**`sources`** — a publisher with a credibility prior. `base_quality` (0–100) starts from the
PRD's table and is a column, not a constant, so observed reliability can update it.
`is_synthetic` marks development-only sources; a check constraint requires them to be `DEMO`.

**`source_documents`** — a retrieved document. `content_hash` is unique *per source*: the same
document re-fetched is a no-op, while identical text republished by another outlet stays a
distinct document so ancestry can fold the two into one confirmation. A check constraint
requires every `DEMO` document to sit on a `.invalid` domain, which RFC 2606 guarantees can
never resolve — a synthetic row cannot be mistaken for a real citation.

**`evidence_clusters`** — a set of documents reporting one underlying announcement, with an
`origin_document_id` (earliest, then highest authority). This table is why ten articles are
one confirmation.

**`evidence_items`** — the atom. A claim-bearing excerpt with character offsets into exactly
one document, a confidence, an optional `event_type` (null for forward-looking statements),
and the `rule_key` of the extraction rule that produced it. Provenance fields are
denormalised onto it so an evidence row is self-describing in the API.

## 2. Intelligence

```
events >── event_evidence ──< evidence_items
signals ──< signal_observations
signals ──< trends >── themes ──< theme_signals
themes ──< theme_company_exposures >── companies
```

**`events`** — a typed occurrence: type, direction, magnitude (0–1), confidence, entity,
subject, `occurred_at`, and the cluster it came from. Because the cluster is part of
`dedupe_key`, five rewrites of one announcement produce **one** event.

**`signals`** / **`signal_observations`** — a tracked phenomenon and its value per 7-day
bucket. Observations store `independent_source_count`, `source_diversity`,
`primary_source_ratio` and the full contribution list in `inputs`, so any strength can be
recomputed by hand.

**`trends`** — the comparison. Observation window versus baseline window, with level,
acceleration, frequency change and independence change, plus both windows' bounds.

**`themes`** — a formed theme with maturity, market awareness, and a separate
`market_awareness_mode` because the awareness estimate is coverage-derived rather than
measured.

**`theme_company_exposures`** — role, order of effect (1st/2nd/3rd), exposure score,
confidence, and `path` — the exact graph hops with their weights. The path is the
explanation.

## 3. Entities and the knowledge graph

**`industries`** (self-referencing taxonomy), **`companies`**, **`securities`**.

`companies.is_fictional` is enforced by a check constraint against `data_mode`: a `DEMO`
company must be fictional and a non-`DEMO` company must not be. Synthetic evidence about a
real ticker is therefore unrepresentable (ADR-006).

**`entity_relationships`** — the graph. Typed, weighted, confidence-bearing edges addressed
as `(entity_type, entity_key)` so one table spans companies, industries, technologies,
products and themes. Edge types: `SUPPLIES`, `BUYS_FROM`, `COMPETES_WITH`, `DEPENDS_ON`,
`BENEFITS_FROM`, `THREATENS`, `SUBSTITUTES_FOR`, `PRODUCES`, `USES`, `REGULATES`,
`INVESTS_IN`, `DRIVES_DEMAND_FOR`.

## 4. Research

```
hypotheses ──< research_runs ──< research_plans ──< research_questions
                    ├──< search_runs
                    ├──< research_findings >── finding_evidence ──< evidence_items
                    ├──< agent_runs
                    └──< research_reports
```

**`agent_runs`** is the trace. Every agent execution writes one — success, failure, timeout
or budget stop — with the strategy, model, prompt version, budget, consumption and both
typed envelopes. The UI's research trace is a read of this table, which is why it cannot
show a run that did not happen.

**`research_findings`** hold no free-floating facts: the evidence links are the fact, and
`claim_type` states whether the sentence is a `FACT`, `INFERENCE`, `HYPOTHESIS` or
`FORECAST`.

## 5. Scoring

**`scores`** + **`score_components`**. A score row carries the model name and version, the
value, `weight_coverage`, and which components were unavailable. Each component row carries
the raw input, the normalised value, the configured weight, the effective weight after
renormalisation, the contribution and a human-readable explanation. `Opportunity 76` is
never a bare number.

---

## Indexes

Timestamps (`event_at`, `published_at`, `occurred_at`, `bucket_start`, `started_at`),
foreign keys used for traversal (`cluster_id`, `theme_id`, `research_run_id`, `signal_id`),
and filter columns (`data_mode`, `maturity`, `event_type`, `subject_key`, graph endpoints).

## Not modelled yet

`User`, `Organization`, `Subscription`, `Watchlist`, `Portfolio`, `PortfolioPosition`,
`Thesis`, `ThesisUpdate`, `Alert`, `Catalyst`, `Risk`, `MarketSnapshot`,
`ValuationSnapshot`, `EvaluationRun`. These are named in the Master Build Prompt's domain
list and are deliberately absent rather than half-modelled: an empty `alerts` table implies
an alerting system that does not exist. They are additive when their features are built.
