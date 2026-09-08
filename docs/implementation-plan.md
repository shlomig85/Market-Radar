# Market Radar — Implementation Plan

**Status:** Cycle 1 (foundation + first vertical slice) — see `decision-log.md` for the reasoning
behind each choice and `architecture.md` for the shape of the system.

---

## 1. Current repository state (discovered)

The repository was, at the start of this cycle, effectively empty:

| Item | Finding |
| --- | --- |
| Files | `README.md` (14 bytes, title only) |
| Git history | one commit (`Initial commit`) |
| Source code | none |
| Framework | none |
| Package manager | none |
| Database | none |
| Configuration / env | none |
| APIs | none |
| Frontend | none |
| Tests | none |
| Deployment config | none |
| Documentation | none |
| Dependencies | none |
| Integrations | none |

**Conclusion: the repository is empty.** There is no existing functionality to preserve, so no
migration or compatibility constraints apply. Every choice below is greenfield.

### Toolchain available in the development environment

Verified directly, not assumed:

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.11.15 | backend language |
| PostgreSQL | 16.13 | server present locally and started successfully |
| Node.js / npm | 22.22.2 / 10.9.7 | frontend |
| Docker | 29.3.1 | available for `docker-compose` parity |
| Redis | server binary present | not used in cycle 1 (see decision log ADR-011) |
| PyPI / npm registries | reachable | dependencies installable |
| **sec.gov / data.sec.gov** | **blocked** | outbound HTTPS to SEC returns `403` at the environment proxy |

That last row is the single most important environmental fact: **no live financial data source is
reachable from this environment.** The architecture is therefore built so that "no live provider"
is a first-class, visible state rather than an excuse to fabricate data.

---

## 2. Proposed architecture (summary)

A **modular monolith**, not microservices:

```
frontend/   Next.js (App Router) + TypeScript + Tailwind — research terminal UI
backend/    Python 3.11 modular monolith
            api/          FastAPI HTTP boundary (thin; no business logic)
            domain/       SQLAlchemy 2.0 models + enums (the vocabulary of the system)
            providers/    replaceable data-source adapters behind interfaces
            ingestion/    document normalisation, hashing, idempotent persistence
            evidence/     deterministic extraction, near-duplicate clustering, independence
            signals/      event -> signal aggregation with historical baselines
            trends/       change detection, acceleration, maturity
            themes/       theme formation from signals
            mapping/      knowledge graph, value chain, company exposure
            scoring/      versioned, weighted, explainable score engine
            agents/       typed agent contracts + Research Planner
            research/     orchestrated research loop and report assembly
db          PostgreSQL 16 (JSONB where the shape is genuinely open-ended)
```

Data flows in one direction:

```
Provider -> SourceDocument -> EvidenceItem -> EvidenceCluster -> Event -> Signal
        -> SignalObservation (time buckets) -> Trend -> Theme
        -> ThemeCompanyExposure (via value chain) -> ResearchPlan -> Findings -> Report -> API -> UI
```

Every arrow is a separately testable module. Nothing downstream may invent information that is not
traceable back to a `SourceDocument`.

---

## 3. Major components

| Component | Responsibility | Deterministic? |
| --- | --- | --- |
| Provider registry | resolve a capability (news, filings, market, company) to a concrete adapter and report its health/mode | yes |
| Ingestion pipeline | normalise provider documents, hash content, persist idempotently with full provenance | yes |
| Evidence extractor | pull claim-bearing excerpts out of documents, attach confidence | yes (rule-based in cycle 1) |
| Ancestry clusterer | group documents that repeat one underlying announcement | yes |
| Independence scorer | convert a set of evidence into *independent* confirmation counts | yes |
| Signal engine | aggregate events into normalised 0–100 signal observations per time bucket | yes |
| Trend engine | compare observation windows against historical baselines to detect **change** | yes |
| Maturity classifier | rule-based stage assignment (INVISIBLE → MATURE/INVALIDATED) | yes |
| Value chain / graph | typed relationships with evidence and confidence; multi-hop propagation | yes |
| Exposure scorer | company exposure from graph position + evidence | yes |
| Score engine | versioned weighted models, per-component inputs stored, renormalised when inputs are unavailable | yes |
| Research Planner agent | theme → typed `ResearchPlan` of questions across 12 dimensions incl. counter-evidence | rule-based by default, LLM optional |
| Research loop | plan → search → sources → evidence → typed findings, with contradiction detection | yes (orchestration) |
| Report assembler | structured findings → report sections with FACT/INFERENCE/HYPOTHESIS labels | yes |

---

## 4. Database architecture

PostgreSQL 16, one schema, Alembic migrations, no ORM-generated DDL in any environment.

Groups of tables:

1. **Provenance:** `sources`, `source_documents`, `evidence_items`, `evidence_clusters`
2. **Intelligence:** `events`, `signals`, `signal_observations`, `trends`, `themes`, `theme_signals`
3. **Entities:** `companies`, `securities`, `industries`, `entity_relationships` (the knowledge graph)
4. **Mapping:** `theme_company_exposures`
5. **Research:** `hypotheses`, `research_runs`, `research_plans`, `research_questions`,
   `search_runs`, `research_findings`, `finding_evidence`, `agent_runs`, `research_reports`
6. **Scoring:** `score_models`, `scores`, `score_components`

Design rules:
- Every row that carries information from the outside world carries a `data_mode`
  (`LIVE` / `HISTORICAL` / `DEMO` / `UNAVAILABLE`). It is not nullable and it propagates downstream.
- Three distinct timestamps everywhere: `published_at`, `event_at`, `retrieved_at`/`ingested_at`.
- `content_hash` is unique per source document → ingestion is idempotent by construction.
- Scores are never stored as a bare number: a score row always has component rows carrying the
  input values, weights, and the score-model version that produced it.
- JSONB is used only where the shape is legitimately open (provider payload snapshots, agent
  input/output envelopes, score input bags). Everything queried is a real column with an index.

Details: `data-model.md`.

---

## 5. Backend architecture

- **FastAPI** for the HTTP boundary. Routers are thin: they validate, call a service, and serialise.
- **SQLAlchemy 2.0** with typed `Mapped[...]` declarative models; synchronous sessions
  (see ADR-004 — async buys nothing at this stage and costs testability).
- **Pydantic v2** for every boundary: HTTP request/response, provider DTOs, and agent I/O schemas.
- **pydantic-settings** for configuration; secrets only from the environment, never committed.
- **structlog** JSON logging with a request id and, where relevant, `research_run_id` / `agent_run_id`
  bound into the context so a report can be traced back through the log stream.
- Domain errors are a small hierarchy (`MarketRadarError` → `ProviderError`, `NotFoundError`, …)
  mapped to HTTP responses in one place. Nothing swallows exceptions silently.
- A **Typer CLI** (`python -m marketradar.cli`) drives ingestion, pipeline execution and research so
  that the whole vertical slice is runnable without the HTTP layer — this is what the end-to-end
  test exercises.

---

## 6. Frontend architecture

- **Next.js App Router**, TypeScript strict, Tailwind. Server components fetch from the backend, so
  no data-fetching library and no client state manager are needed yet.
- Two routes in cycle 1: `/` (dashboard) and `/themes/[slug]` (theme page). Both are real views over
  real API responses; neither contains a control that does not work.
- A `DataModeBadge` primitive renders the provenance mode of anything on screen. If the backend says
  `DEMO`, the user sees `DEMO`.
- Visual language: dense, monospaced numerics, dark research-terminal palette. No chat UI.

---

## 7. AI / agent architecture

Cycle 1 deliberately ships **one** agent, and it is not a prose generator.

- `Agent[InputT, OutputT]` — an abstract base with a typed input schema, a typed output schema, an
  explicit budget (`max_llm_calls`, `max_searches`, `max_seconds`), stopping rules, and failure
  handling. Every execution writes an `agent_runs` row: inputs, outputs, timings, status, model,
  prompt version, token/cost counters.
- `ResearchPlannerAgent` — theme + signals → `ResearchPlan`. Two interchangeable strategies behind
  the same contract:
  - `RuleBasedPlanStrategy` (default, deterministic, no network, no key) — expands the theme across
    12 research dimensions including mandatory counter-evidence and historical-analogue questions.
  - `LlmPlanStrategy` (used only when `ANTHROPIC_API_KEY` is configured) — same output schema,
    validated by Pydantic before it is allowed into the database.
- The strategy that produced a plan is recorded on the plan. The UI shows it. A rule-based plan is
  never displayed as if an LLM reasoned about it.
- Retrieved documents are treated strictly as untrusted data; the prompt scaffolding separates
  system instructions from source content, and source text is never executed as instruction.

Details: `agent-architecture.md`.

---

## 8. Data ingestion architecture

Interfaces first, vendors second:

```python
class NewsSearchProvider(Protocol):
    def search(self, query: ProviderQuery) -> ProviderResult: ...
    def get_document(self, external_id: str) -> ProviderDocument | None: ...
    def health(self) -> ProviderHealth: ...
```

Same shape for `FilingsProvider`, `MarketDataProvider`, `CompanyDataProvider`. A `ProviderRegistry`
resolves capability → adapter from configuration, and every adapter must answer `health()` with a
`DataMode`. When an adapter is not configured or not reachable, the registry returns an
`UnavailableProvider` whose mode is `UNAVAILABLE`; downstream code — including the score engine —
handles that explicitly instead of substituting a number.

Cycle 1 adapters:
- `FixtureNewsProvider`, `FixtureFilingsProvider`, `FixtureCompanyProvider` — serve the labelled
  demo corpus (`DEMO`).
- `SecEdgarFilingsProvider` — a real adapter against `data.sec.gov` with an injectable HTTP
  transport. Its parsing is unit-tested; **its live path is unverified in this environment because
  outbound access to sec.gov is blocked**, so the registry reports it `UNAVAILABLE` here.
- `UnavailableMarketDataProvider` — the honest default. No market-data vendor is configured, so
  market-derived score components are omitted and the omission is shown to the user.

---

## 9. Background processing

Cycle 1 runs the pipeline as explicit, idempotent, resumable **steps** invoked from the CLI, behind
a `PipelineStep` interface and an in-process `EventBus` (`SOURCE_INGESTED`, `EVENT_CREATED`,
`SIGNAL_CREATED`, `TREND_UPDATED`, `RESEARCH_COMPLETED`, …).

This is deliberate: the step boundaries and the event contract are the parts that a queue needs, and
they are what is being proven now. Swapping the in-process bus for a worker pool (arq/Celery on
Redis) is a change of dispatcher, not a change of architecture. It is scheduled for cycle 2, when
there is a continuously-running ingestion source that justifies it.

---

## 10. Testing strategy

| Level | What it covers |
| --- | --- |
| Unit | hashing, freshness/timestamp handling, evidence extraction, near-duplicate clustering, ancestry, independence scoring, signal normalisation, baseline/acceleration maths, maturity rules, exposure propagation, score renormalisation, planner output shape |
| Integration | real PostgreSQL: model persistence, constraints, idempotent re-ingestion, provider registry health, API endpoints |
| End-to-end | `source → event → signal → trend → theme → research → report`, asserted on database state, run against real PostgreSQL |
| Property-ish | duplicate evidence must not raise independence; unavailable inputs must not silently change a score |

Tests run against a real PostgreSQL database (`marketradar_test`), not SQLite, so that JSONB,
constraints and index behaviour are exercised as they will be in production.

Details: `evaluation-plan.md` covers the *AI* evaluation layer, which is designed in cycle 1 and
implemented in cycle 3.

---

## 11. Local development strategy

```
make setup     # install backend deps, install frontend deps
make db-up     # start postgres (docker compose, or local cluster)
make migrate   # alembic upgrade head
make seed      # load the labelled DEMO corpus
make pipeline  # run ingestion -> events -> signals -> trends -> theme
make research  # run the research loop and produce a report
make test      # pytest
make api       # uvicorn on :8000
make web       # next dev on :3000
```

`.env.example` documents every variable. No secret is ever committed. `docker-compose.yml` provides
PostgreSQL for developers who do not have a local cluster.

---

## 12. Implementation phases and their dependencies

```
P0 discovery + docs
      │
P2 foundation (config, db, migrations, domain models, api skeleton)
      │
P3 provider boundary + data modes ────┐
      │                               │
P4 provenance + ancestry + independence│
      │                               │
P5 signal + trend engine + scoring     │
      │                               │
P6 value chain + company exposure ─────┘
      │
P7 research planner agent (typed I/O, budget, trace)
      │
P8 research loop (plan → search → evidence → findings)
      │
P9 report assembly
      │
P10 UI (dashboard + theme page)
      │
Tests at every phase; e2e once P5 exists, extended once P9 exists.
```

Hard dependencies:
- P4 must precede P5: a signal computed from evidence of unknown provenance is worthless.
- P5 must precede P6: exposure is only meaningful relative to a detected change.
- P6 must precede P7: the planner needs companies to ask company-exposure questions about.
- P8 must precede P9: a report that is not assembled from stored findings cannot be traced.

### Deferred to later cycles (explicitly not built in cycle 1)

Authentication and multi-tenancy; Bull/Bear/Skeptic/Historical/Valuation/Catalyst/Risk/Investment
Committee agents; live news, market-data and alternative-data providers; embeddings/pgvector
semantic clustering; job queue and schedulers; alerting; thesis guardian and monitoring; daily
brief; natural-language query interface; backtesting and the golden dataset; portfolio intelligence;
personalisation; company page; causal-graph visualisation.

`decision-log.md` records why each was deferred rather than stubbed.
