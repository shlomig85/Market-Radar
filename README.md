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
* **Companies ranked 0-10** at `/trending`, each row expanding into the components that
  produced the rating and the ones that could not be computed. A company the rising theme
  works *against* is listed and labelled `headwind`, never dropped and never shown as if it
  benefited.

## What is deliberately absent

No authentication. No market data, so valuation, mispricing, catalyst scoring and price
confirmation are reported `UNAVAILABLE` and excluded from scores rather than estimated. One
agent, not sixteen — no
Bull, Bear, Skeptic, Valuation, Catalyst, Risk or Investment Committee agent exists. No
alerts, thesis monitoring, daily brief, backtesting or portfolio features.

Live providers do exist and are verified — SEC company tickers, SEC EDGAR filings, and
sixteen free public RSS/Atom feeds (see `docs/data-sources.md`). They are **off by default**:
with nothing configured the pipeline runs on a clearly labelled synthetic corpus with
fictional issuers, and every figure it produces is stamped `DEMO`. Turning them on is a
matter of environment variables, documented under *Running against real sources* below.

See the cycle report and `docs/implementation-plan.md` §12 for the full list.

### The demo universe is fictional on purpose

Every company in the development corpus is invented (`Northbridge Memory Corp`, `NBMX`),
every publisher is marked synthetic, and every URL is on a `.invalid` domain that RFC 2606
guarantees cannot resolve. Database check constraints make it *impossible* to store a `DEMO`
company that is not fictional, or a `DEMO` document on a resolvable URL. Synthetic evidence
about a real, tradeable security is unrepresentable rather than merely discouraged — see
`docs/decision-log.md` ADR-006.

---

## Run it on your machine

### With Docker — one command

```bash
make up          # or: ./scripts/start.sh
```

Then open **http://localhost:3000/trending** — the ranked list of companies with a 0-10
trend rating, where each row expands into the reasons for the rating. **http://localhost:3000**
is the theme dashboard behind it. The API and its OpenAPI docs are at
**http://localhost:8000/docs**. Stop with `make down`.

`make up` starts PostgreSQL, applies migrations, seeds the fictional DEMO universe, runs the
pipeline and the research loop, and only then starts the API and the web server — so the
first page you open already has a theme, a value chain and a report on it. Every step is
idempotent, so running it again is safe.

The first build takes a few minutes; later starts take seconds. Data persists in a Docker
volume; `docker compose down -v` clears it.

### Without Docker — host processes

Requirements: **Python 3.11+** (macOS ships 3.9 as `python3` — the code uses `datetime.UTC`
and will not run on it), Node 20+, and a running PostgreSQL 16 with a `marketradar`
database and role. `./scripts/start.sh --native` checks the version and says so rather than
failing with an ImportError.

To run a one-off command without installing anything, use the container, which bundles the
right Python:

```bash
docker compose --profile cli run --rm cli python -m marketradar.cli <command>
```

The `cli` service depends only on the database, so a one-off command never waits on — or is
blocked by — the demo bring-up, and it publishes no host ports, so it cannot collide with
another running stack.

```bash
./scripts/start.sh --native
```

Or step by step, which is the same sequence and easier to debug:

```bash
cp .env.example .env
make setup        # install backend + frontend dependencies
make db-up        # start PostgreSQL (skip if you already run a local cluster)
make migrate      # apply migrations
make seed         # load the fictional DEMO reference universe
make pipeline     # ingest -> events -> graph -> signals -> trends -> themes -> scores
make research     # plan -> search -> findings -> report
make api          # http://localhost:8000  (docs at /docs)
make web          # http://localhost:3000/trending  (dashboard at /)
```

`make all` runs the whole chain from an empty database to a rendered report.
`make help` lists every target.

To see companies ranked by how strongly they are caught up in something changing, open
**http://localhost:3000/trending** — each row expands into the components that produced its
rating, including the ones that could not be computed. The same list in the terminal:

```bash
make trending                       # locally
make docker-cli cmd="trending"      # or against the Docker stack's database
```

The rating is **0-10**, aggregated from theme trend, exposure strength and independent
corroboration — every input already stored with its own evidence trail. Two things on every
row are deliberate: `MODE` says whether a rating rests on real or synthetic evidence, and
`DIR` says whether a rising theme is a **tailwind** or a **headwind** for that company. A
competitor of a beneficiary is exposed to the same move and does not benefit from it.

Price confirmation is `UNAVAILABLE` — there is no market-data provider — so its weight is
redistributed rather than estimated, and the rating cannot tell an unnoticed move from one
already priced in.

To see what the corpus turned out to be about — topics found, not declared:

```bash
make subjects                       # locally
make docker-cli cmd="subjects"      # or against the Docker stack's database
```

`found` means discovered from the documents; `declared` means it came from the built-in
lexicon. A subject must be supported by several **independent** ancestry clusters, so one
syndicated story cannot create one, and it must be a term this corpus uses at least a
hundred times more often than general English does (ADR-029). That second test is why
`memory` is not a subject but `high-bandwidth memory` is: English says the first constantly
and the second essentially never.

To check which news sources are actually answering:

```bash
make feeds                          # locally
make docker-cli cmd="feeds"         # or against the Docker stack's database
```

Every source is free and public — no API keys anywhere. The list is weighted toward
statutory bodies (SEC, Federal Reserve, BLS, BEA, Census, Treasury, EIA, ECB), because they
publish the numbers everyone else reports *on*; industry and technical press sit below them,
and general financial media lowest, since it mostly re-reports both. Ancestry clustering
then counts ten outlets rewriting one release as **one** confirmation.

Feed URLs rot. `feeds` probes each one and prints what it returned, so a dead endpoint is a
five-second discovery rather than a quiet absence. Replace the list entirely with
`MARKETRADAR_FEEDS` — which sources you trust is your call, not ours.

To see the knowledge graph and what each edge rests on:

```bash
make graph                          # locally
make docker-cli cmd="graph"         # or against the Docker stack's database
```

Every edge prints with the sentence that asserts it and the URL of the document it came from.
An edge that was seeded by hand rather than read out of a document prints
`(no evidence — hand-entered)` — that label is the point, not an oversight.

### Running against real sources

**The real sources are the default** (ADR-025). What they are NOT is anonymous: publishers
block unidentified scrapers and the SEC requires a contact address under its fair-access
policy, so both refuse to fetch rather than send an anonymous request.

One command sets it up:

```bash
make configure EMAIL=you@example.com
```

That writes your contact string and the provider choices into `.env`, creating it from
`.env.example` if you have none and rewriting the values in place if you do — which matters,
because a `.env` you copied before the defaults changed still says `fixture`, and editing the
example can never reach a file you already have.

> Do not paste those settings at a shell prompt. The contact string contains a space, so
> `MARKETRADAR_FEED_USER_AGENT=Market Radar you@example.com` is a *command* whose second word
> is `Radar`, and the shell says `command not found: Radar`. That is why `make configure`
> exists.

The containers read `.env` too, so this is also the last time you need `-e` flags on a
`docker compose run`. Then:

```bash
docker compose down -v && docker compose up -d     # Docker
make docker-cli cmd="pipeline --rebuild"

make reset && make pipeline                        # or host processes
```

Until a contact string is set the news capability reports `UNAVAILABLE`, and the trending
page is empty and names the variables to set rather than showing you anything invented.

The full source list and the reliability argument behind it is in
[`docs/data-sources.md`](docs/data-sources.md). To run one command against real sources
without touching `.env`:

```bash
docker compose --profile cli run --rm \
  -e MARKETRADAR_NEWS_PROVIDER=feeds \
  -e MARKETRADAR_FEED_USER_AGENT="Market Radar your@email" \
  -e MARKETRADAR_COMPANY_PROVIDER=sec \
  -e MARKETRADAR_FILINGS_PROVIDER=sec_edgar \
  -e MARKETRADAR_SEC_USER_AGENT="Market Radar your@email" \
  cli python -m marketradar.cli pipeline --rebuild
```

Put the same five variables in `.env` to make them the default for the whole stack, API and
web included.

Two things to expect on the first real run. **Invented companies never appear** in the
trending list (ADR-026): if the synthetic corpus is also in the database its issuers are
filtered out rather than badged, so a list that looks short may simply be excluding them —
`make reset` clears it. And **a feed or two will fail**: they move, rot, and return 403 to
unfamiliar clients. `make feeds` says which, and a failed feed is simply absent rather than
compensated for.

`MARKETRADAR_SUBJECT_EXCLUSIONS` takes a comma-separated list of terms that should never
become subjects. Discovery removes site furniture by detecting sentences a publisher repeats
verbatim, which catches most of it; this is the operator's veto for the rest.

### Troubleshooting

| Symptom | Cause |
| --- | --- |
| "API unreachable" on the dashboard | the backend is not running, or `MARKETRADAR_API_URL` points at the wrong host |
| "No themes have formed yet" | the pipeline has not run — `make pipeline`. This is also the honest answer whenever nothing is accelerating |
| Theme page shows no research trace | `make research` has not run for that theme. The panel reflects the database rather than rendering a placeholder |
| Output still looks wrong after a fix that should have changed it | derived data can persist in ways a version bump does not reach. `marketradar pipeline --rebuild` discards every derived artefact and rebuilds from the stored documents. Documents, sources, companies and hand-curated graph edges are kept, so nothing is re-fetched |
| A pipeline re-run reports `created=0 skipped=N` and nothing changes | expected when the corpus and the extractors are both unchanged — documents are not re-fetched. If an extractor was *improved*, its version must be bumped: the pipeline then retracts the previous version's output and re-extracts (ADR-017) |
| Port 3000 or 8000 already in use | change the host side of the port mapping in `docker-compose.yml` |
| `Bind for 0.0.0.0:5432 failed: port is already allocated` | another Market Radar stack (or a local Postgres) already holds the port. Either stop it — `docker compose ls` names the running projects — or give this stack its own ports: `export MARKETRADAR_POSTGRES_HOST_PORT=5433 MARKETRADAR_API_HOST_PORT=8001 MARKETRADAR_WEB_HOST_PORT=3001` |
| `service "bootstrap" didn't complete successfully: exit 1` | the demo bring-up failed. It now prints `bootstrap FAILED during: <step>` — read that line first: `docker compose logs bootstrap`. A one-off CLI command does not need bootstrap at all; use the `cli` service below |
| A one-off command drags in the whole demo bring-up | `docker compose run --rm api ...` depends on `bootstrap`. Use `docker compose --profile cli run --rm cli ...` instead — it depends only on the database and publishes no host ports |
| `No such command '<name>'` after a `git pull` | the `cli` service mounts the working tree, so this means the pull did not land. Check `git log --oneline -1`. Only a dependency change (`pyproject.toml`) needs `docker compose build cli`; code changes do not |
| `api` behaves differently from `cli` after a pull | expected: `cli` runs the working tree so debugging always shows current code, while `api` and `bootstrap` run the built image so `docker compose up` stays reproducible. `docker compose build` realigns them |

---

## Testing

```bash
make test              # 356 tests
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
| `docs/decision-log.md` | 30 ADRs — what was decided, what was rejected, what it costs |
| `docs/data-model.md` | every table and the conventions behind them |
| `docs/data-sources.md` | every free public source read, and why it can be trusted |
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
