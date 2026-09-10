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

---

## ADR-017 — An extractor version bump retracts the previous version's output

**Decision.** `EXTRACTOR_VERSION` and `RELATIONSHIP_EXTRACTOR_VERSION` are bumped whenever extraction
*behaviour* changes, not only when the file changes. A bump causes `ingestion/retraction.py` to
withdraw everything the previous version produced — evidence, events, extracted edges, and findings
left with no support — before re-extraction runs. `entity_relationships.extractor_version` (migration
0003) marks an edge as derived; a curated edge has NULL there, survives every retraction, and only
loses its citation.

**Alternatives.** (a) Bump the version and leave superseded rows in place. (b) Require a full
`reset` after any extractor change. (c) Version nothing and always re-extract everything.

**Why.** Extraction is idempotent on `(document, span, rule, extractor version)`, which is correct
right up to the moment the rules change without the version changing — then every document looks
already-done and the improvement never reaches stored data. This was observed, not theorised: rules
that produced a fabricated customer edge were fixed and verified, the pipeline was re-run against the
database holding that edge, and it reported `created=0 skipped=72` and left the edge in place.

(a) is worse than doing nothing: the corpus then carries two generations of evidence and
double-counts them into events. (b) throws away the documents, which are the expensive part and the
part that is not derived — and on a rate-limited source, re-fetching to fix a regex is absurd. (c)
gives up idempotency, which is what makes a partial failure safe to re-run.

**Also (2026-09-09).** The same rule applies to **discovered subjects**. A run that predated the
website-furniture filter left "cookie preference" and "reprint permission advertising" in the
`subjects` table long after the filter was in place, because refresh only ever added rows. A
discovered subject not in the current candidate set is now withdrawn — unless evidence still
references it, since retracting it would strand those rows. Declared lexicon subjects are never
withdrawn: discovery did not create them, so it does not get to remove them.

**Cost.** Retraction is destructive by design, so the boundary between derived and curated data has
to be exactly right — hence the new column, rather than inferring intent from whether an edge happens
to carry a citation. Research reports are *not* deleted: a report is a published artefact and quietly
destroying one is worse than telling its reader it is stale, so retraction counts them and says so.
Until the report is re-run it may cite evidence that no longer exists.


---

## ADR-018 — Discovery starts with subscribed feeds from declared-quality publishers

**Decision.** News and blog ingestion is an RSS/Atom feed provider (`providers/feeds.py`) over a
list of publishers, each carrying its own `base_quality` and `SourceClass`. The default list ships
in code as a starting point; `MARKETRADAR_FEEDS` replaces it entirely. Article bodies are fetched
and extracted; when an article cannot be retrieved the publisher's own abstract is stored instead,
labelled `body_source: "summary"`. A feed that fails is `UNAVAILABLE`, never an empty result.

**Alternatives.** (a) Keep the CIK watchlist and add nothing. (b) A general web crawler. (c) A paid
news or web-search API. (d) Scrape search-engine results.

**Why.** The system could only see companies someone had typed into `MARKETRADAR_SEC_CIKS`, which
makes discovery impossible by construction — you cannot find a trend you had to name first. Feeds
are the cheapest honest fix: a feed is a publisher's own declaration of what it published *and
when*, so `published_at` is stated rather than guessed, which matters because every as-of read
depends on knowing when something became knowable. A crawler (b) would have to infer publication
time, which is exactly the guess that fabricates urgency. (c) remains a drop-in behind the same
provider interface and needs a key the operator may not have; nothing here forecloses it. (d) is
brittle, usually against terms of service, and gives no reliability signal at all.

Quality is *declared per publisher* rather than inferred, so independence scoring can already tell a
statutory source from a personal blog — the machinery for that existed and had nothing real to score.

**Free and public is a hard constraint** (stated by the operator, 2026-09-09): no entry in the
default list requires an API key, a subscription or a login, and a test asserts it. Reliability is
therefore bought with *source class* rather than with money — statutory bodies (SEC, Federal Reserve,
BLS, BEA, Census, Treasury, EIA, ECB) carry the list at quality 93–96, industry and technical press
sit at 72–80, and general financial media lowest at 68–70 because it mostly re-reports the first two.
Ancestry clustering then collapses those re-reports into one confirmation, attributed upstream.

**Cost.** Feeds cover recent items only, so there is no archive and no backfill: the corpus starts
the day ingestion starts. Feed URLs also rot, and a dead feed is indistinguishable from a quiet news
day — `marketradar feeds` probes every subscribed endpoint and reports what it returned, because the
default URLs cannot be verified from the build environment and should be pruned by whoever runs it. Feeds have no server-side search, so `search()` is a client-side filter
over recent items and says so rather than implying it searched the web. The default publisher list
is unverified from the build environment — `live-check` reports which feeds actually answered, and
the list is a starting point rather than a recommendation. Article extraction is heuristic; a
paywall yields an abstract, and an abstract supports weaker claims than an article, which is why the
distinction is recorded on every document.

---

## ADR-019 — Subjects are discovered from the corpus; the extractor's own vocabulary is the anti-vocabulary

**Decision.** Subjects are mined from stored documents (`subjects/discovery.py`) and persisted
(`subjects` table, migration 0004). A term qualifies on **independent-cluster support**, not
frequency. Signal definitions became templates (`SIGNAL_TEMPLATES`) instantiated against whatever
subjects carry evidence, so a discovered subject is measured without anyone editing code. Words the
evidence extractor keys on are **excluded** from subject candidacy, derived from the rules themselves.

**Alternatives.** (a) Keep the hand-typed three-subject lexicon. (b) Mine by raw frequency or TF-IDF.
(c) Ask an LLM to propose topics.

**Why.** (a) is audit finding C6: a system that only recognises what it was taught monitors, it does
not discover. (b) was tried and is recorded here because the failure is instructive — the first run
over a real corpus returned *capacity, pricing, demand, inventory, declined*. Every one is a
predicate. Of course it is: a corpus of documents about things changing shares the vocabulary of
change, so frequency finds it. Those are already modelled as `EventType`; a subject is the thing
change happens **to**. Deriving the exclusion from the extraction rules makes it self-maintaining —
add a rule keying on a new change-word and it stops being a candidate subject on the next run.

Three further filters each fix an observed failure: company n-grams are excluded (an issuer is an
entity resolution already handles, and admitting it lets one company's coverage read as a theme); a
term already covered by a declared subject's vocabulary is not duplicated (otherwise one topic's
evidence splits across two keys on a tie-break); and equal-support containment collapses "grid",
"storage" and "grid storage" into the phrase, since words that only ever co-occur are one unit.

**Cost.** The stopword list is still hand-maintained and English-only, and it is a real hand-tuned
surface — the honest limit of a rules-based miner. Recall on subjects is unmeasured: there is no
labelled set saying which topics a corpus "should" yield, so the qualifying thresholds
(3 clusters, 0.75 document ratio above 20 documents) are reasoned, not calibrated. Discovery also
cannot name a topic in words the corpus does not use, and with no feed archive the corpus begins the
day ingestion begins, so emergence is weak until history accumulates.

---

## ADR-020 — The 0-10 trend rating aggregates stored measurements; it never adds a new one

**Decision.** `scoring/company_trend.py` rates companies 0-10 from four components — theme trend,
theme acceleration, exposure strength, independent corroboration — plus a fifth, price
confirmation, that is **UNAVAILABLE** and has its weight redistributed. The rating is the stored
0-100 score divided by ten, to one decimal. The *strongest* theme sets a company's rating; further
themes add a bounded bonus (max +18%). Companies exposed as competitors or substitutes are labelled
`headwind`, not `tailwind`.

**Alternatives.** (a) Sum contributions across every theme. (b) Rate on raw evidence volume. (c)
Report the rating to two decimals.

**Why.** (a) lets a company weakly adjacent to five unrelated themes outrank one at the centre of a
real move, purely on breadth — a ranking artefact, not a finding. Breadth is corroborating, so it
adds a capped bonus instead. (b) is the failure this whole system is built against: volume is
mostly syndication, and corroboration is therefore counted in independent ancestry clusters. (c)
would imply precision no component has; the inputs are reasoned weights over rule-extracted
evidence, so a second decimal is decoration.

The headwind label matters more than it looks. A competitor of a beneficiary is genuinely exposed to
a rising theme and does not benefit from it. Listing both under one "trending" heading is the single
most misleading thing this output could do, so direction is carried on every row.

**Cost.** The component weights (0.35 / 0.20 / 0.25 / 0.15 / 0.05) are reasoned, not calibrated
against outcomes — `docs/evaluation-plan.md` holds the backtest that would validate them, and it is
not built. Without market data, price confirmation is never available, so in practice the rating
runs on four components and cannot tell an unnoticed move from one already priced in. And because
feeds carry no archive, acceleration is weak until a corpus accumulates: early ratings measure a
short history and should be read as provisional.

---

## ADR-021 — Boilerplate is found by verbatim repetition, not by proportion

**Decision.** Before any candidate term is generated, subject discovery identifies sentences that
appear **verbatim in four or more documents** and removes them from every document. Sentences under
five words are exempt. This replaces the per-source saturation ratios, which are deleted from the
decision path.

**Alternatives.** (a) A corpus-wide document-frequency ceiling. (b) Per-publisher saturation. (c)
Per-publisher saturation plus a cross-publisher contrast. (d) A hand-maintained list of known
boilerplate phrases.

**Why.** (a), (b) and (c) were each tried against live data and each failed, and the reason is the
same every time: **proportion cannot separate a footer from a topic.** A publisher's footer sat in
roughly 13% of its own documents — article extraction succeeds on some page templates and not others
— so no saturation threshold reached it without also rejecting genuine topics. (a) could not see it
at all, since one site's furniture is a small share of a multi-publisher corpus. (c) added a
contrast between publishers, which a corpus dominated by one publisher does not have. (d) does not
generalise past the sites someone thought of.

The tell in the live output was that thirty reported "subjects" shared *identical* statistics — same
cluster count, same emergence, same salience. That is not thirty topics; it is one block of text.
Verbatim repetition is the property that block actually has, and it depends on neither how much a
publisher wrote nor how many publishers exist. Two independently written articles do not share a
sentence; a footer is the same sentence every time. Syndicated copy does repeat, but ancestry
clustering already collapses it to a single confirmation, so removing it costs nothing that was
going to count.

**Cost.** A genuinely repeated sentence in real editorial content is removed — a standard disclosure
in every filing from one issuer, say. That text is not lost from the corpus, only from *subject
candidacy*, and evidence extraction still reads it. The four-document threshold and five-word floor
are reasoned, not calibrated: too low and a common phrase disappears, too high and a footer on three
documents survives. Both are constants at the top of the module rather than buried in the logic.

**Also.** Three test fixtures had to be rewritten because they repeated one sentence across every
document. They were correctly identified as boilerplate — which is the clearest evidence the rule
does what it claims, and a reminder that synthetic corpora are unrealistically uniform in exactly
the way that matters here.

---

## ADR-022 — Noise is excluded by category, never by adding words one run at a time

**Decision.** Three category-level exclusions replace reactive word-adding:

* **Common English** — roughly the thousand most frequent English words, *minus* those that
  are also plausible investment subjects (memory, energy, storage, data, battery, …).
* **Publisher names** — derived from the `Source` rows, exactly as company names already were.
* **Normalisation before filtering** — tokens are folded to their comparison form *before* the
  stopword test, not after.

**Alternatives.** (a) Keep extending the hand-picked list after each live run. (b) A part-of-speech
tagger to keep only noun phrases. (c) An LLM to judge whether a term is a topic.

**Why.** (a) is what was happening, and it does not converge: each run returned a fresh batch of
ordinary English — "expert", "strong", "leader", "concern", "worth", "asked", "meanwhile",
"increasingly" — and each fix covered only the words that run happened to surface. Word frequency is
**linguistic** knowledge, not domain knowledge: a common-English list names no industry, technology
or product, so it does not reintroduce the hardcoded-vocabulary problem (C6) that discovery exists
to remove. (b) is a dependency and a model to keep current for a job three category rules do. (c)
cannot be audited and costs money per run.

The subtraction is the part that matters. A term is rejected when it *begins or ends* with a
stopword, so leaving "memory" in the common list would reject "high-bandwidth memory" — the flagship
subject — and leaving "energy" in would reject "grid energy storage".

Normalising before filtering fixed a bug that had been silently defeating every filter above it:
"expert" was a stopword, "experts" was not, and the term became "expert" anyway. Every plural in
English was walking through the entire chain.

**Cost.** The common-word list and the domain-plausible subtraction are both judgement calls, and
the subtraction in particular is the one place domain knowledge re-enters — a word wrongly left in
silently costs a real subject. Both are single constants at the top of the module rather than
conditions buried in the logic, so the judgement is inspectable. A publisher whose name is also a
genuine topic would be excluded outright; none of the current sources has that problem.


---

## ADR-023 — The trending list recomputes on read rather than serving the last stored rating

**Context.** Every company rating is persisted as a `Score` row when the pipeline runs, so
`GET /trending` could simply read the newest rows and sort them. It does not: it calls
`rate_companies(..., persist=False)` on every request.

**Decision.** Recompute. The endpoint reads themes, exposures and stored theme scores from the
database and aggregates them at request time, writing nothing.

**Alternatives.** (a) Serve the stored `Score` rows. (b) Serve stored rows and show the
`computed_at` timestamp next to them. (c) Recompute.

**Why.** (a) is the defect this project has already been bitten by twice — derived data
outliving the evidence it was derived from, and a fix that never reaches what the reader sees
(the `created=0 skipped=72` incident, ADR-017). A stored rating is a claim about the corpus as
it stood at some past moment, and a trending list that silently reflects last week's run is
precisely the kind of number this system exists not to produce. (b) is honest but asks the
reader to do the reasoning: a timestamp does not tell them whether anything has changed since.
(c) costs a few hundred milliseconds of aggregation over data that is already in memory, and
the aggregation itself introduces no measurement — every input was computed and stored by an
earlier stage with its own evidence trail.

**Cost.** The endpoint is O(exposures) per request rather than O(1), so it will need a cache
once the corpus is large enough for that to matter. The cache key must be the pipeline run,
not a clock — a time-based TTL would reintroduce exactly the staleness this avoids. The stored
`Score` rows remain the historical record: they are what makes "what did we think in March?"
answerable, which is a different question from "what do we think now?" and must not be served
by the same code path.

---

## ADR-024 — Headwind companies are shown and labelled, never filtered out

**Context.** A rising theme does not help every company exposed to it. A competitor of a
beneficiary, or the maker of a substitute being displaced, sits on the same value chain and
scores highly on the same exposure machinery.

**Decision.** They appear in the ranked list by default, coloured differently and labelled
`headwind`, with the tooltip stating that the theme works *against* them. The API defaults to
`include_headwinds=true`; the flag exists but the default is to show.

**Alternatives.** (a) Filter them out of "trending". (b) Rate them and invert the sign.
(c) Show them, labelled.

**Why.** (a) hides real information: the fact that a rising theme is bad news for a specific
company is often the more actionable half of the observation, and a reader who cannot see it
will infer that the list is a buy list. (b) asserts something not measured — that the harm is
proportional to the benefit, which no component here estimates. (c) reports what was actually
computed: this company is strongly exposed, and the exposure runs against it.

**Cost.** A reader who skims only the numbers can misread a high headwind rating as a
recommendation. The colour, the direction column, the row label and the panel footnote are
four separate places where the distinction is stated, which is the mitigation available
without either hiding data or inventing a number.

---

## ADR-025 — The real sources are the default; the synthetic corpus must be asked for

**Context.** `news_provider`, `filings_provider` and `company_provider` all defaulted to
`fixture` — the synthetic DEMO corpus. An operator who ran `make up` without reading the
configuration got a ranked list of companies that do not exist.

**Decision.** The defaults are `feeds`, `sec_edgar` and `sec`. The synthetic corpus is still
present and still necessary — it is what lets the pipeline and the whole test suite run
without a network — but it is now something you select rather than something you receive by
not selecting anything.

**Alternatives.** (a) Keep `fixture` as the default and rely on the `DEMO` badges.
(b) Default to the real providers. (c) Refuse to start until the operator chooses.

**Why.** (a) was the state of the world, and the badges were not enough: a reader looking for
stocks to research reads the tickers, not the provenance chrome. A fabricated ticker in a
ranked list is worse than an empty list, because an empty list is obviously not an answer and
a fabricated one is not obviously anything. (c) turns a first run into a configuration
exercise and teaches nothing.

The cost of (b) is real and worth stating: publishers and the SEC both refuse anonymous
requests, so with no contact string configured the news capability resolves `UNAVAILABLE` and
a first run produces nothing at all. That is the correct failure — "I have no sources" rather
than "here are eight companies I invented" — and the empty state on the trending page names
the two variables to set.

**Cost.** An existing `.env` copied from the old `.env.example` still says `fixture`, so
this change does not reach anyone who already has one; they have to edit it. The test suite
now pins every provider in `conftest`, at the environment level rather than only on the
`settings` fixture, because code reached through the API builds its own registry from
process-wide settings and would otherwise go to the network during a test run.

---

## ADR-026 — A fictional issuer is never listed among stocks, badge or no badge

**Context.** `DataMode` and the `DEMO` badge are the system's general answer to "this figure
is not real". The trending page is a list of companies a reader might act on, which makes it
a different kind of surface.

**Decision.** `GET /trending` excludes companies with `is_fictional = true` by default.
Seeing them requires `include_fictional=true` on the API, or the explicit `?demo=1` URL in the
UI, where every row is additionally stamped `INVENTED — NOT A REAL COMPANY`.

**Alternatives.** (a) Rely on the existing `DEMO` badge. (b) Exclude them entirely, with no
way to see them. (c) Exclude by default, with an explicit opt-in.

**Why.** (a) puts the burden of not being misled on the reader, on the one screen where being
misled is expensive. (b) removes the ability to check the layout and the pipeline end to end
before any network is configured, which is exactly when a new operator most needs to see that
the thing works. (c) keeps both properties: the default is safe, and the escape hatch is a URL
you have to type rather than a control you can nudge by accident.

**Cost.** Two code paths through the same list, and a default-empty page during setup. The
empty state carries the opt-in link so it is a signpost rather than a dead end.

---

## ADR-027 — The reason line counts independent clusters, and says so when the rest is syndication

**Context.** Every row now carries a plain-language sentence explaining why the company is
listed. The obvious number to put in it is how many publishers wrote about the company.

**Decision.** The sentence reports the count of independent ancestry clusters among *that
company's own* evidence. When more publishers than clusters carried it, the sentence says so:
"2 sources reported it independently of each other, across 5 publishers — the rest are running
the same story."

**Alternatives.** (a) Publisher count. (b) Cluster count alone. (c) Both, with the
relationship stated.

**Why.** (a) flatters the evidence exactly where it matters most: five outlets running one
wire story is one source, and reporting it as five would undo the clustering the entire system
is built on. (b) is honest but throws away something a reader wants — that a story travelled.
(c) states the measurement and the reach, and makes the difference between them legible, which
is the single most useful thing this product knows that a news feed does not.

Two counts were conflated in the first version of this and it is worth recording. The rating
already carried `independent_clusters`, which counts the *theme's* corroboration; the sentence
used it, and it can exceed the company's own publisher count, so a company known from one
article was described as corroborated by seven sources. They are now separate fields
(`independent_reports` for the company, `independent_clusters` for the theme) and a test
asserts they are not interchangeable.

**Cost.** Both counts are computed over every matching evidence row rather than the six
displayed, so the numbers are stable when the display is truncated — at the cost of a count
that does not visibly match the list beneath it. Stating a weaker claim than the display
suggests is the right direction for that error to run.

---

## ADR-028 — The rebuild's delete order comes from the schema, not from a hand-written list

**Context.** `rebuild_derived()` deleted every derived artefact by iterating a tuple of model
classes in a hand-chosen order. The tuple named six tables. The schema has two more that
carry a non-nullable foreign key to `themes` — `hypotheses` and `research_runs`, both added
when the research loop was written — and neither was ever added to it. `pipeline --rebuild`
therefore died with `ForeignKeyViolation ... fk_hypotheses_theme_id_themes` for any database
where the research loop had ever run, which includes every database created by
`docker compose up`, because bootstrap runs it.

**Decision.** Delete in reverse of `Base.metadata.sorted_tables`, which SQLAlchemy already
orders topologically by dependency. Two explicit sets carve out the exceptions:
`PRESERVED_TABLES` (documents, sources, clusters, companies, securities, industries) and
`SELECTIVELY_CLEARED_TABLES` (curated graph edges and declared subjects, where only some rows
are derived).

**Alternatives.** (a) Add the two missing models to the tuple. (b) `TRUNCATE ... CASCADE` on
the derived tables. (c) Derive the order from the schema.

**Why.** (a) fixes today's crash and leaves tomorrow's in place: the tuple is a copy of the
dependency graph that has to be maintained by hand, and it has already gone stale once
without anyone noticing for weeks. (b) is fast, but `CASCADE` deletes whatever happens to
reference the table — including, potentially, something in the preserved set — which is the
wrong shape of instruction for an operation whose whole purpose is to keep the expensive data.
(c) has a single source of truth, and the failure mode inverts: forgetting to *preserve* a new
table costs a re-fetch, while forgetting to *delete* one leaves stale data outliving its
evidence, which is the defect class this whole module exists to prevent.

A test asserts every table is named in one of the two sets or is deleted by default, and that
neither set names a table the schema no longer has. Adding a table now forces the decision at
the point it is added.

**Cost.** The delete is now bulk SQL rather than ORM deletes, so ORM-level cascades and event
hooks no longer fire during a rebuild. Nothing currently depends on them — the database
constraints carry the relationships — but that is an assumption this note makes explicit
rather than leaving to be discovered.
