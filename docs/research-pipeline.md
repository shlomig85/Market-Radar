# Research pipeline

## The deterministic pipeline

```
provider documents
   │  ingestion/pipeline.py: normalise, hash, persist with provenance   [idempotent]
   ▼
source_documents
   │  evidence/clustering.py: declared origin → exact hash → shingle similarity
   ▼
evidence_clusters                       ← ten articles become one confirmation here
   │  evidence/extractor.py: rule-based, span-anchored claim extraction
   ▼
evidence_items
   │  ingestion/pipeline.py: group by (cluster, type, subject, day)
   ▼
events
   │  signals/engine.py: per-cluster, quality-weighted, saturating
   ▼
signal_observations (7-day buckets)
   │  trends/engine.py: observation window vs baseline window
   ▼
trends
   │  themes/formation.py: co-accelerating signals over adjacent subjects
   ▼
themes → mapping/value_chain.py → theme_company_exposures
       → themes/scoring.py     → scores + score_components
       → trends/maturity.py    → maturity + market awareness
```

Run it with `make pipeline`. Every stage is idempotent; re-running produces identical
database state, which an end-to-end test asserts.

### The formation gate

A signal joins a theme only if **acceleration ≥ 20** against its own baseline **and** it has
**≥ 2 independent sources** in the observation window. A subject can be enormously
well-covered and never form a theme. That is the intended behaviour and the difference
between this and a topic detector.

Independence is counted across the whole window by distinct evidence cluster, not as a
per-bucket maximum — two sources reporting in consecutive weeks are two sources.

---

## The research loop

```
theme
  │  _ensure_hypothesis
  ▼
hypothesis
  │  ResearchPlannerAgent  →  agent_runs row
  ▼
research_plan + research_questions        (12 dimensions, counter-evidence mandatory)
  │  for each question, across each available provider
  ▼
search_runs        (query, provider, provider mode, hits, new documents, latency, status)
  │  ingest new documents → recluster → re-extract
  ▼
evidence
  │  research/findings.py — one finding per dimension
  ▼
research_findings >── finding_evidence ──< evidence_items
  │  research/report.py
  ▼
research_report
```

Run it with `make research theme=ai-memory-demand`.

### Findings

One finding per **dimension**, not per question: evidence gathering keys on the dimension, so
several questions about demand would otherwise produce duplicate findings that read as extra
corroboration. The finding records how many questions it answers.

A finding carries:

* `claim` — counts and a breakdown by event type, generated from the evidence;
* `claim_type` — `FACT` (single observed event type), `INFERENCE` (aggregates several), or
  `HYPOTHESIS` (built only from forward-looking or historical statements);
* `stance` — supporting, contradicting, or neutral. Risk and contradiction dimensions are
  contradicting by construction; market expectations and historical analogue are neutral,
  because reported price movement is awareness, not counter-evidence;
* independence numbers and the evidence rows themselves.

**"We searched and found nothing" is a finding.** A dimension with no evidence produces an
explicit statement that this is a fact about our search, not about the world — a silent gap
in the research is indistinguishable from a gap in reality, and the first is our problem.

### Forward-looking statements

Sentences that speculate ("we see risk that…") or reason by analogy ("historically, capacity
additions of this scale…") are extracted as evidence but carry **no event type**. They inform
the bear case and the historical-analogue dimension; they never move a signal. Without this,
"we see risk that supply expands faster than demand" would be counted as evidence of demand
acceleration — a defect this rule was written to fix.

---

## The report

Assembled deterministically from stored findings (ADR-009). Sections:

`what_changed` · `why_now` · `why_it_matters` · `supporting_evidence` ·
`contradictory_evidence` · `contextual_findings` · `companies_exposed` · `market_awareness` ·
`invalidation_conditions` · `research_gaps` · `scores` · `provenance`

Every line carries a `claim_type` and the ids of the evidence rows behind it, so "where did
this come from?" resolves through `finding → evidence → document → source` for anything the
UI renders. `invalidation_conditions` are expressed as measurable rules against named signals
with thresholds, so a monitoring cycle can evaluate them automatically — that evaluation is
not implemented, and the UI says so.

The `provenance` block records the generator and version, the pipeline version, the as-of
instant, the mode of every provider capability, and the research disclaimer.

---

## Search strategy

Searches are generated from the hypothesis, decomposed across dimensions, and always include
counter-evidence terms. In this build the only searchable providers serve the DEMO corpus, so
searches quickly reach saturation and stop — which is the stopping rule working, and is
recorded as `stop_reason: saturation` rather than presented as thoroughness.

## Reproducibility

A historical report can be reproduced from what is stored: the model and prompt version (or
the fact that none was used), the scoring model versions, the extractor and pipeline
versions, the exact search queries and providers, the evidence rows used, and the as-of
instant.
