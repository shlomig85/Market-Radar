# Market Radar — Architecture Decision Log

Each entry: the decision, the alternatives considered, why this one, and what it costs.
Entries are append-only. Superseding an entry means adding a new one that says so.

---

## ADR-001 — Modular monolith, not microservices

**Decision.** One deployable backend (`backend/marketradar`) with hard module boundaries
(`providers`, `ingestion`, `evidence`, `signals`, `trends`, `mapping`, `scoring`, `agents`,
`research`, `api`) and a one-directional dependency rule.

**Alternatives.** Service-per-stage (ingestion service, research service, scoring service); a
framework-heavy plugin architecture.

**Why.** There is exactly one team and no scaling pressure yet. The genuinely hard problems here are
evidence quality, independence and explainability — none of which are made easier by a network hop.
The Master Build Prompt explicitly warns against premature microservices.

**Cost.** A future need to scale ingestion independently will require extracting a service. The
module boundaries and the event bus are what make that extraction mechanical rather than a rewrite.

---

## ADR-002 — Python backend + Next.js frontend, not one language

**Decision.** Python 3.11 for everything behind the API; TypeScript/Next.js for the UI.

**Alternatives.** All-TypeScript (single language, single package manager); all-Python with a
server-rendered template UI.

**Why.** The Master Build Prompt recommends exactly this split, and the reasoning holds: the
backend's future is numerical and AI-orchestration work, where Python's ecosystem is decisive; the
frontend's future is a dense, interactive research terminal, where React is decisive.

**Cost.** Two toolchains, two dependency systems, and a typed contract that must be kept in sync by
hand. Mitigated by keeping the API surface small and the response schemas explicit.

---

## ADR-003 — PostgreSQL only; no separate vector database

**Decision.** PostgreSQL 16 for relational data, JSONB for genuinely open-ended payloads. No vector
store, no graph database, no search engine in cycle 1.

**Alternatives.** Neo4j for the knowledge graph; Elasticsearch for document search; Pinecone/Qdrant
for embeddings.

**Why.** The graph is currently thousands of edges, not billions — a `entity_relationships` table
with a recursive traversal is faster to query and far easier to reason about. Semantic clustering is
not yet required because ancestry detection at this corpus size is solved well by exact hashing plus
shingle similarity. Introducing a second datastore multiplies operational surface for no measured
benefit. `pgvector` is the planned next step if and when embedding-based clustering is justified.

**Cost.** Multi-hop graph queries will need care as the graph grows; the traversal is currently
implemented in Python over indexed lookups, bounded by hop count.

---

## ADR-004 — Synchronous SQLAlchemy, not async

**Decision.** `sqlalchemy` 2.0 synchronous sessions; FastAPI runs sync path operations in its
threadpool.

**Alternatives.** `asyncpg` + async sessions throughout.

**Why.** The workload is CPU/logic-bound pipeline work and low-concurrency reads, not thousands of
concurrent connections. Sync code is materially simpler to test (no event-loop fixtures) and it
removes a large class of "forgot to await" bugs. The cost of switching later is contained because
persistence is accessed through repository-style functions rather than scattered session calls.

**Cost.** Under high API concurrency the threadpool becomes the limit. That is far away, and
measurable when it arrives.

---

## ADR-005 — Tests run against real PostgreSQL, never SQLite

**Decision.** The test suite provisions a real `marketradar_test` database.

**Alternatives.** SQLite in-memory for speed, with portable column types.

**Why.** The schema depends on JSONB, on check constraints, on unique partial behaviour and on
Postgres-specific defaults. A SQLite suite would pass while production diverges — the worst kind of
green. Postgres is available locally and in CI via docker-compose, so the cost is a few seconds.

**Cost.** Tests require a running database. `make test` starts one; the fixture fails loudly with
setup instructions rather than silently falling back.

---

## ADR-006 — The demo universe uses fictional issuers

**Decision.** The labelled `DEMO` corpus describes a real-world *phenomenon* (AI-driven memory
demand, DRAM/HBM/NAND, memory manufacturing, semiconductor equipment) but every **company** in it is
fictional (e.g. `Northbridge Memory Corp`, ticker `NBMX`), every publisher is explicitly synthetic,
and every URL is on a `.invalid` domain, which by RFC 2606 can never resolve.

**Alternatives.** (a) Use real tickers (MU, SK Hynix, ASML, …) with synthetic articles about them.
(b) Ship no demo data at all until a live provider is available.

**Why.** Option (a) means generating fabricated statements, numbers and quotes attributed to real,
publicly traded issuers, stored in a database, rendered in an investment-research UI. That is the
exact failure mode both source documents forbid — "never fabricate live financial information", "do
not make fake data look like live data" — and no amount of badge-labelling makes a fabricated
earnings quote about a real company safe to keep in a research system. Option (b) leaves the
architecture unproven. Fictional issuers prove the value-chain and exposure architecture exactly as
well, because the code path is identical, while making it impossible for a demo row to be mistaken
for a real claim about a real security.

**Cost.** The demo dashboard is less immediately impressive than one showing familiar tickers. That
is the intended trade: the honest version is the point. Real issuers enter the system through the
company-data provider (SEC company tickers), which is implemented and will populate real entities as
soon as a live provider is reachable.

---

## ADR-007 — `UNAVAILABLE` is a first-class value, and scores renormalise around it

**Decision.** Missing inputs are represented as `UNAVAILABLE`, never as `0`, never as a default, and
never omitted silently. A score model computes over its *available* components, renormalises the
remaining weights, records which components were dropped, and exposes that list through the API.

**Alternatives.** Substitute a neutral 50; substitute 0; refuse to compute the score at all.

**Why.** Substituting a value invents information and corrupts every comparison between themes.
Refusing to compute makes the product useless whenever any vendor is missing — which, given the
environment, is always. Renormalising with a visible "computed without: market_mispricing,
catalyst_proximity" is both honest and usable, and it makes the effect of adding a data provider
measurable.

**Cost.** Scores computed from different component sets are not perfectly comparable. The API
therefore always ships the component list alongside the number, and the UI shows it.

---

## ADR-008 — Deterministic first, LLM second — including for the first agent

**Decision.** Evidence extraction, clustering, independence, signals, trends, maturity, exposure and
all scoring are deterministic code with unit tests. The one agent shipped (Research Planner) defaults
to a deterministic rule-based strategy and uses an LLM strategy only when a key is configured; both
satisfy the same Pydantic output schema, and the strategy used is recorded and displayed.

**Alternatives.** LLM-first with deterministic fallback; LLM-only.

**Why.** Everything an LLM would do in these steps is either arithmetic or classification with a
small label set — where determinism is more accurate, free, instant, reproducible and testable. It
also means the vertical slice is genuinely runnable in an environment with no API key, which is the
current environment. Reserving the LLM for judgement (hypothesis construction, debate, synthesis)
matches the PRD's cost-routing guidance.

**Cost.** The rule-based planner produces less imaginative research questions than a good model
would. It is a floor, not a ceiling, and the seam to raise it is already in place.

---

## ADR-009 — Reports are assembled from structured findings, not written by a model

**Decision.** `ResearchReport` sections are built deterministically from `ResearchFinding` rows, and
every claim carries a `ClaimType` of `FACT` / `INFERENCE` / `HYPOTHESIS` / `FORECAST` plus links to
the evidence rows supporting it.

**Alternatives.** Prompt a model with all the evidence and store its prose.

**Why.** Generated prose is where provenance dies: it is precisely the step at which unsupported
sentences enter a research product. Assembling from structured findings makes "every claim traceable"
a structural property rather than a prompt instruction that mostly works. Narrative polish can be
layered on later *from* the structured report, and would then be labelled as generated.

**Cost.** Cycle-1 reports read like a structured briefing rather than an analyst's essay. Given the
choice between eloquence and verifiability, the source documents are unambiguous: evidence >
eloquence.

---

## ADR-010 — Live SEC adapter is written but reported `UNAVAILABLE` in this environment

**Decision.** `SecEdgarFilingsProvider` is implemented against the real `data.sec.gov` endpoints with
an injectable HTTP transport, and its parsing layer is unit-tested against recorded-shape fixtures.
Because outbound access to `sec.gov` returns `403` at this environment's proxy, its `health()`
reports `UNAVAILABLE` here and the pipeline runs on the `DEMO` corpus.

**Alternatives.** Omit the adapter entirely; or ship it and describe the slice as "live".

**Why.** Omitting it would leave the provider abstraction untested against a real API shape.
Describing it as live would be false. The middle path — real code, real interface, honest health
reporting, and an explicit statement that the live path is *unverified in this environment* — keeps
the boundary proven without overclaiming.

**Cost.** The live path carries the residual risk of any code that has not been run against the real
endpoint. It is called out in the README, in the cycle report, and in the provider's own docstring.

---

## ADR-011 — No queue, no Redis, no scheduler in cycle 1

**Decision.** The pipeline runs as explicit idempotent steps behind a `PipelineStep` interface, driven
by a CLI, with an in-process `EventBus` defining the event contract.

**Alternatives.** Celery or arq on Redis from day one.

**Why.** There is no continuously arriving data yet — every provider that would produce it is
unavailable. Adding a broker now would mean writing worker plumbing whose behaviour cannot be
exercised, while the parts that actually determine whether a queue is easy to adopt (step
idempotency, the event vocabulary, resumability) are being built and tested now.

**Cost.** Cycle 2 must add the worker layer. Because every step is already idempotent and
content-hash keyed, that is a dispatcher change.

---

## ADR-012 — No authentication in cycle 1

**Decision.** The API ships without authentication and is documented as a single-tenant development
surface. `User`/`Organization` are absent from the schema rather than half-modelled.

**Alternatives.** Ship a token check now; model users now and enforce later.

**Why.** There is no multi-user requirement in the vertical slice, and a half-built auth boundary
invites the belief that a boundary exists. Naming its absence is safer than a token gate that has
never been threat-modelled. The domain model has no user-scoped rows yet, so adding tenancy later is
additive.

**Cost.** The backend must not be exposed publicly before cycle 2. Stated in the README and in the
cycle report's risk list.

---

## ADR-013 — Three timestamps, always

**Decision.** `published_at`, `event_at` and `retrieved_at` are separate non-collapsible columns, and
signal recency is computed from `event_at`, not from publication.

**Alternatives.** A single `timestamp` column with a type discriminator.

**Why.** The PRD is explicit: an article published today about a 2023 event is not a new signal.
Collapsing these is the standard way that recency-weighted intelligence systems fabricate urgency.
Keeping them apart makes the correct behaviour the default and the incorrect behaviour impossible to
write by accident.

**Cost.** Providers often supply only a publication date. `event_at` is then explicitly inferred and
the inference is recorded on the document (`event_at_inferred = true`), rather than being invisible.

---

## ADR-014 — Near-duplicate threshold set to 0.60, erring toward merging

**Decision.** The shingle-similarity threshold for near-duplicate clustering defaults to
**0.60** (5-word shingles), not the initially chosen 0.72.

**Alternatives.** A stricter 0.72–0.80, which merges only near-verbatim copies.

**Why.** The two error modes are not symmetric. A *false merge* treats two genuinely
independent reports as one and **understates** independence — the system becomes too
cautious. A *false split* treats one story reported five times as five confirmations and
**overstates** independence — which is the exact failure this product exists to prevent. The
asymmetry says to err toward merging.

The number was then checked rather than guessed: sweeping 0.50–0.80 over the development
corpus produced an identical clustering at every threshold (no false merges appear even at
0.50), while a realistically reworded syndication of the corpus's flagship announcement
scores 0.66 — caught at 0.60, missed at 0.72. Two independently written articles about the
same event typically score 0.3–0.4 on 5-word shingles, so 0.60 retains a wide margin.

**Cost.** The threshold is calibrated against a synthetic corpus, which is weak evidence.
It is a configuration value (`MARKETRADAR_DUPLICATE_SIMILARITY_THRESHOLD`), and
`docs/evaluation-plan.md` schedules re-validation against labelled real documents before it
can be trusted. Longer term, embedding similarity should replace shingles for rewrites that
share meaning but little vocabulary.

---

## ADR-015 — Value-chain edges are read out of filings, and an edge is only as good as its citation

**Decision.** `entity_relationships` rows are produced by a rule extractor over filing text
(`marketradar/entities/relationships.py`), each carrying an `evidence_id` that points at the exact
sentence asserting it. When an existing hand-entered edge is first cited, its confidence **drops to
the confidence of that citation** rather than keeping its seeded prior.

**Alternatives.** (a) Keep curating edges by hand, which is how the graph started. (b) Extract edges
but keep the higher of the seeded and evidenced confidence. (c) Infer edges statistically from
co-occurrence of company names in the same document.

**Why.** The Cycle-1 audit's C5 finding was that every edge had been typed in by a human and not one
carried evidence. A traversal over such a graph produces exposure paths that look like findings and
are actually assumptions — the failure mode this product exists to avoid. Extraction fixes the
provenance; the confidence rule fixes the honesty. A hand-entered 0.95 backed by a sentence worth
0.78 is 0.78, and presenting it as 0.95 would launder a guess into a measurement. Co-occurrence was
rejected outright: two companies named in one document are related far less often than not, and the
resulting edges would carry a document reference that does not actually assert the relationship.

**Cost.** Recall. Measured on held-out filing phrasings the rules were not fitted to:
**precision 1.00, recall 0.29** (`tests/unit/test_relationship_extraction.py`). The rule set reads a
closed list of disclosure phrasings and is silent on the rest, so the graph it builds is sparse and
skewed toward companies that write plainly. That trade is deliberate — a missing edge costs reach, a
wrong edge propagates through every traversal that crosses it and is indistinguishable from a right
one — but it is the strongest argument for an LLM extractor behind the same interface, scored on the
same held-out set.

**Also.** The extractor is first-person (`"our suppliers include ..."`), so it needs to know who "we"
is. Documents with no identified filer — news articles, industry reports — yield no edges rather than
edges attributed to a guess.

---

## ADR-016 — A theme anchors on the companies its own evidence names, not only on concepts

**Decision.** Theme-to-company mapping starts from two kinds of anchor: the concept nodes a subject
declares (`SUBJECT_ANCHORS`), and the **companies named by the evidence that formed the theme**
(`mapping/exposure.evidence_anchors`). A company anchor is first-order by construction and carries a
reason instead of a hop path. Separately, evidence extracted from a filing falls back to the filer as
its entity when the sentence names no company.

**Alternatives.** (a) Concept anchors only, as before. (b) Extend the concept lexicon until real
filings hit it. (c) Attach every company mentioned anywhere in a document to the theme.

**Why.** A live SEC run formed a genuine theme from 72 real filings and mapped it to **zero**
companies. A knowledge graph read out of filings is almost entirely company-to-company — filings
state who supplies whom, and rarely describe their own products in whatever vocabulary a concept
lexicon happens to contain — so a traversal that can only start at concept nodes starts at entities
the graph does not have. Extending the lexicon (b) is the same bet that produced the empty result,
one vocabulary later, and is the hardcoded-subject problem (audit C6) wearing a different hat. (c)
would attach a company to a theme for being mentioned, which is not evidence of exposure.

The companies a theme's evidence is *about* are the entry point that always exists, and they are the
most direct exposure there is. The filer fallback is the same principle one level down: a 10-K's
"demand for our products increased" is a claim about the filer, and treating it as entity-less threw
away most of what real filings say. On the development corpus the fallback took company-attributed
events from 3 to 26.

**Cost.** Anchor weight is a judgement, not a measurement: it saturates as `1 - e^(-clusters/2)`
against the subject's own anchor weight, so a company named by one cluster anchors at 0.39 and by
five at 0.92. The shape is borrowed from signal strength for consistency; the constant is not
calibrated against anything. An evidence-anchored exposure also has no hop path, so `path.hops` is
empty and `path.anchored_by` says `"evidence"` — the UI and any reader must treat the two kinds of
explanation differently rather than assuming every exposure has a traversal behind it.
