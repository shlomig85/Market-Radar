# Market Radar

An emerging-theme investment intelligence platform: it detects *change* in the world,
connects it to public companies through a value chain, researches it with evidence that
stays traceable to its source, and ranks the result as a **research priority** — never as a
price forecast.

> **Cycle 1 status.** This repository contains the foundation and one complete vertical
> slice. Roughly a sixth of the PRD is implemented. What is missing is named as missing, in
> this README, in the UI, and in `docs/`. Nothing is mocked to look finished.

---

## What actually works

```
provider → document → evidence → cluster → event → signal → trend → theme
        → company exposure → research plan → search → findings → report → UI
```

Every arrow runs end to end and is covered by tests. Concretely, on the development corpus:

* **34 documents → 30 evidence clusters → 42 events.** The five documents reporting one
  announcement collapse into a single confirmation.
* **52 evidence items → 11 independent sources** (amplification ratio 0.21).
* Seven signals computed against 126 days of history; five accelerate enough to form the
  **AI Memory Demand** theme, classified `ACCELERATING` by deterministic rules.
* Eight companies mapped by graph traversal at first, second and third order, each with the
  exact causal path that reached it.
* Trend 73 · Confidence 84 · Opportunity 76 — each with a full component decomposition and
  an explicit list of components that could not be computed.
* A research run: 17 planned questions across 12 dimensions (counter-evidence mandatory),
  6 searches, 12 findings of which 4 contradict the thesis, stopped on saturation.

## What is deliberately absent

No authentication. No live data — outbound access to `sec.gov` is blocked in the development
environment, so the pipeline runs on a **clearly labelled synthetic corpus with fictional
issuers**. No market data, so valuation, mispricing and catalyst scoring are reported
`UNAVAILABLE` and excluded from scores rather than estimated. One agent, not sixteen — no
Bull, Bear, Skeptic, Valuation, Catalyst, Risk or Investment Committee agent exists. No
alerts, thesis monitoring, daily brief, backtesting or portfolio features.

See the cycle report and `docs/implementation-plan.md` §12 for the full list.

### The demo universe is fictional on purpose

Every company in the development corpus is invented (`Northbridge Memory Corp`, `NBMX`),
every publisher is marked synthetic, and every URL is on a `.invalid` domain that RFC 2606
guarantees cannot resolve. Database check constraints make it *impossible* to store a `DEMO`
company that is not fictional, or a `DEMO` document on a resolvable URL. Synthetic evidence
about a real, tradeable security is unrepresentable rather than merely discouraged — see
`docs/decision-log.md` ADR-006.

---

## Quick start

Requirements: Python 3.11+, Node 20+, PostgreSQL 16 (or Docker).

```bash
cp .env.example .env
make setup        # install backend + frontend dependencies
make db-up        # start PostgreSQL (skip if you have a local cluster)
make migrate      # apply migrations
make seed         # load the fictional DEMO reference universe
make pipeline     # ingest → events → signals → trends → themes → scores
make research     # plan → search → findings → report
make api          # http://localhost:8000  (docs at /docs)
make web          # http://localhost:3000
```

`make all` runs the whole chain from an empty database to a rendered report.
`make help` lists every target.

### Inspect from the terminal

```bash
make providers                      # which capabilities have data, and in what mode
make show theme=ai-memory-demand     # scores with full decomposition and the value chain
make stats                           # row counts across the intelligence tables
```

---

## Testing

```bash
make test              # 138 tests
make test-unit         # no database required
make test-e2e          # the full vertical slice
```

Tests run against a **real PostgreSQL** database (`marketradar_test`), never SQLite: the
schema depends on JSONB and check constraints, and a SQLite suite would pass while
production diverged (ADR-005). The schema is built by running the migrations, so every test
run also verifies that migrations produce the schema the models expect.

---

## Layout

```
backend/marketradar/
  config.py logging.py errors.py bus.py orchestration.py cli.py
  db/           engine, session, column types
  domain/       enums + 27 SQLAlchemy models
  providers/    interfaces, registry, fixtures, SEC EDGAR, SSRF-safe HTTP
  ingestion/    hashing, idempotent persistence, event construction
  evidence/     extraction, ancestry clustering, independence
  signals/      definitions (data) + engine
  trends/       change detection + maturity rules
  themes/       formation + metrics + score assembly
  mapping/      knowledge graph traversal + exposure
  scoring/      versioned models + renormalising engine
  agents/       typed agent contract + Research Planner
  research/     loop, findings, report assembly
  api/          FastAPI routers, schemas, read services
  demo/         the synthetic corpus and its loader
frontend/       Next.js dashboard + theme page
docs/           architecture, decisions, data model, scoring, pipeline, evaluation
```

## Documentation

| Document | Contents |
| --- | --- |
| `docs/implementation-plan.md` | repository state, architecture, phases and dependencies |
| `docs/architecture.md` | layering, pipeline, provenance, data modes, security posture |
| `docs/decision-log.md` | 14 ADRs — what was decided, what was rejected, what it costs |
| `docs/data-model.md` | every table and the conventions behind them |
| `docs/scoring-model.md` | weights, formulas, and the calibration debt |
| `docs/research-pipeline.md` | the deterministic pipeline and the research loop |
| `docs/agent-architecture.md` | agent contract, budgets, traces, injection posture |
| `docs/evaluation-plan.md` | how this gets validated — designed, not built |

---

## Security posture

Implemented: no secrets in the repository; external documents treated strictly as data;
SSRF allowlist on every outbound fetch; Pydantic validation at every boundary; parameterised
queries only; structured audit-grade logging of every agent and research run.

**Not implemented:** authentication, authorisation, tenant isolation, rate limiting,
encryption at rest. The API is a single-tenant development surface and **must not be exposed
publicly** until the auth boundary exists (ADR-012).

---

Research output only. Not investment advice, and not a forecast of any security's price.
