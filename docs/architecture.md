# Market Radar — Architecture

This document describes the system as built in cycle 1. Anything not built is named in
`implementation-plan.md §12` and is absent from this document on purpose.

---

## 1. The one-sentence architecture

Market Radar is a modular monolith that turns **documents with provenance** into **measured change**
into **explainable, evidence-linked research** — with every downstream artefact traceable back to a
source document, and with anything it does not know rendered as `UNAVAILABLE` rather than guessed.

---

## 2. Layering

```
                    ┌──────────────────────────────────────┐
                    │  frontend/ (Next.js, server-rendered) │
                    └───────────────────┬───────────────────┘
                                        │ HTTP (JSON)
                    ┌───────────────────▼───────────────────┐
                    │  api/       FastAPI routers + schemas  │   thin
                    ├───────────────────────────────────────┤
                    │  research/  orchestration + report     │
                    │  agents/    typed agents + traces      │
                    ├───────────────────────────────────────┤
                    │  scoring/   versioned score models     │
                    │  mapping/   graph, value chain         │
                    │  trends/    change detection           │
                    │  signals/   aggregation + baselines    │
                    │  evidence/  extraction, ancestry       │
                    │  ingestion/ normalise + persist        │
                    ├───────────────────────────────────────┤
                    │  providers/ replaceable adapters       │   boundary
                    ├───────────────────────────────────────┤
                    │  domain/    SQLAlchemy models + enums   │
                    │  db/        engine, session, types      │
                    └───────────────────┬───────────────────┘
                                        │
                              PostgreSQL 16
```

Dependency rule: a layer may import from the layers below it and never from the layers above it.
`domain/` imports nothing from the application layers. `providers/` knows nothing about signals,
trends or scores. This is enforced by convention and by the module layout, and it is what makes a
provider or an agent replaceable.

---

## 3. The pipeline

```
 ProviderDocument              (external, untrusted, typed DTO)
        │  ingestion/pipeline.py — normalise, hash, dedupe, persist
        ▼
 SourceDocument                (provenance: url, publisher, 3 timestamps, hash, data_mode)
        │  evidence/extractor.py — rule-based claim extraction
        ▼
 EvidenceItem                  (excerpt + claim + confidence, points at exactly one document)
        │  evidence/clustering.py — near-duplicate + ancestry
        ▼
 EvidenceCluster               (N documents that repeat ONE underlying announcement)
        │  ingestion/events.py — typed occurrence in the world
        ▼
 Event                         (entity, event_type, direction, magnitude, confidence, event_at)
        │  signals/engine.py — bucket, weight by source quality, adjust for independence
        ▼
 SignalObservation             (signal x time bucket -> strength/confidence/independent count)
        │  trends/engine.py — compare window vs baseline
        ▼
 Trend                         (acceleration, direction, baseline stats, novelty)
        │  themes/formation.py — group trends that describe one phenomenon
        ▼
 Theme                         (+ scores via scoring/, + maturity via trends/maturity.py)
        │  mapping/ — graph traversal from theme concepts to companies
        ▼
 ThemeCompanyExposure          (role, order (1st/2nd/3rd), exposure score, evidence, confidence)
        │  agents/planner.py -> research/loop.py
        ▼
 ResearchPlan -> SearchRun -> EvidenceItem -> ResearchFinding
        │  research/report.py
        ▼
 ResearchReport                (sections, each claim labelled FACT/INFERENCE/HYPOTHESIS)
```

Each step is idempotent and re-runnable. Re-running the whole pipeline on the same corpus produces
the same database state (verified by an integration test).

---

## 4. Provenance model (non-negotiable)

`EvidenceItem` is the atom. It carries, or reaches by one foreign key:

| Field | Where it lives |
| --- | --- |
| source, publisher, source_type, source quality | `sources` |
| source_url, content_hash, title | `source_documents` |
| published_at / event_at / retrieved_at | `source_documents` (three distinct columns) |
| data_mode | `source_documents` and denormalised onto `evidence_items` |
| excerpt, claim, confidence, char offsets | `evidence_items` |
| cluster + whether this is the cluster's origin | `evidence_clusters` |

A `ResearchFinding` has no free-text facts of its own: it links to evidence rows through
`finding_evidence`. The API can therefore answer "where did this claim come from?" for every claim
in a report by walking `finding → evidence → document → source`, and the UI renders exactly that
chain.

### Information ancestry

Ten articles rewriting one announcement must not read as ten confirmations. `evidence/clustering.py`
assigns documents to an `EvidenceCluster` using, in order:

1. exact `content_hash` equality (identical bytes),
2. normalised-shingle Jaccard similarity above a configured threshold (rewrites/syndication),
3. shared `origin_ref` — an explicit "this document cites that announcement" pointer supplied by the
   provider or extracted during ingestion.

The **earliest** document in a cluster, preferring the highest-authority source type, becomes the
cluster origin. `evidence/independence.py` then computes, for any evidence set:

```
independent_source_count   = number of distinct clusters
underlying_event_count     = number of distinct origin events
source_diversity           = normalised entropy over source classes
primary_source_ratio       = share of evidence from PRIMARY-class sources
```

Signal strength consumes those numbers, not the raw document count. This is the single most
important anti-noise mechanism in the system.

---

## 5. Data modes

```python
class DataMode(StrEnum):
    LIVE = "LIVE"                # a configured, reachable, real provider, now
    HISTORICAL = "HISTORICAL"    # real data, but a past snapshot / backfill
    DEMO = "DEMO"                # synthetic, generated for development
    UNAVAILABLE = "UNAVAILABLE"  # we do not have it and we will not invent it
```

Rules enforced in code:
- The demo loader may only write `DEMO`. A database check constraint keeps demo documents on
  `*.invalid` URLs so a demo row can never be mistaken for a live citation.
- Mode propagates: a theme's effective mode is the *weakest* mode among its evidence.
- Score components whose inputs are `UNAVAILABLE` are **omitted, and the remaining weights are
  renormalised**, with the omission recorded on the score and shown in the UI. A missing input never
  becomes a zero and never becomes a silent default.

---

## 6. Change detection, not topic detection

`signals/` produces, for each `(signal, bucket)`, a strength in 0–100 computed from
independence-adjusted, source-quality-weighted, recency-weighted event magnitudes.

`trends/engine.py` then compares an **observation window** against a **baseline window**:

```
obs      = mean strength over the last  W_obs days
baseline = mean strength over the prior W_base days
accel    = normalise( (obs - baseline) / max(baseline, floor) )
```

plus the change in *event frequency* and the change in *independent cluster count* over the same
windows. A topic that is merely popular has a high absolute strength and a near-zero acceleration,
and the engine ranks it accordingly. That is the difference between
*"many articles mention AI memory"* and *"AI-related memory demand evidence is rising against its own
baseline"*, and it is arithmetic, not an opinion.

Maturity (`INVISIBLE → EMERGING → DEVELOPING → ACCELERATING → CONSENSUS → CROWDED → MATURE`, plus
`INVALIDATED`) is assigned by ordered rules over measurable inputs: independent cluster count,
acceleration, mainstream-coverage share and market awareness. An LLM does not assign maturity.

---

## 7. Scoring

A `ScoreModel` is a named, versioned set of weighted components (`trend_v1`, `opportunity_v1`).
Computing a score writes:

- one `scores` row: value, model name + version, timestamp, and which components were unavailable;
- one `score_components` row per component: raw input, normalised value, weight, effective weight
  after renormalisation, contribution, and a human-readable explanation string.

So `Opportunity Score: 74` is never a bare number — the API returns its decomposition and the UI
renders it. Weights live in `scoring/models.py` as data, not scattered through the code, and
changing them requires a new version id, which is stored on every score computed with it.

The scores are explicitly *research-priority* scores. Nothing in the system claims a probability
that a security's price will rise, and `confidence` is always reported separately from
`opportunity`, because "very interesting, poorly evidenced" is a real and useful state.

---

## 8. Knowledge graph and value chain

`entity_relationships` is a typed, evidence-bearing edge table over companies, industries,
technologies, products and themes:

```
SUPPLIES · BUYS_FROM · COMPETES_WITH · DEPENDS_ON · BENEFITS_FROM
THREATENS · SUBSTITUTES_FOR · PRODUCES · USES · REGULATES · INVESTS_IN
```

Every edge carries `confidence`, `weight`, an optional evidence link and a `data_mode`.

`mapping/value_chain.py` performs a breadth-first traversal from a theme's anchor concepts outward,
decaying confidence per hop, which yields first-, second- and third-order exposure without the graph
being hardcoded. The AI-memory chain (AI infrastructure → server deployment → memory requirement →
DRAM/HBM/NAND → manufacturers → equipment → upstream) is **seed data expressed in that generic
model**, used as the first validation case — not a special code path.

---

## 9. Agents

Agents are typed functions with budgets and traces, not chat sessions.

```python
class Agent(ABC, Generic[InputT, OutputT]):
    name: str; version: str
    input_model: type[InputT]; output_model: type[OutputT]
    def run(self, ctx: AgentContext, payload: InputT) -> AgentResult[OutputT]: ...
```

`run()` always writes an `agent_runs` row (status, timings, model, prompt version, budget consumed,
input and output envelopes) whether it succeeds, fails, times out or is stopped by a budget rule.
The research trace shown in the UI is that table — it is a record of what happened, and if no agent
ran, there is no trace to show.

Cycle 1 implements `ResearchPlannerAgent` only. Bull/Bear/Skeptic/Valuation and the Investment
Committee are specified in `agent-architecture.md` and are not implemented; the UI does not pretend
otherwise.

---

## 10. Security posture (cycle 1)

Implemented: no secrets in the repository (`.env` only, `.env.example` documents the keys); external
documents treated as untrusted data and never as instructions; provider URL handling restricted to
`https` with an allowlist check before any outbound fetch (SSRF); Pydantic validation at every
boundary; parameterised queries only; structured audit-grade logging of every agent and research run.

Not implemented, and therefore not claimed: authentication, authorisation, tenant isolation, rate
limiting, encryption at rest. The API is a single-tenant development surface in cycle 1 and is
documented as such.

---

## 11. Observability

`structlog` emits JSON with a bound `request_id`, and `research_run_id` / `agent_run_id` where
applicable. Durable traces live in the database (`agent_runs`, `search_runs`, `research_runs`), which
is what makes a historical report reproducible: it records the model, prompt version, scoring model
version, provider modes, search queries and the exact evidence rows that were used.
