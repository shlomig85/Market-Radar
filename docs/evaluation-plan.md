# Evaluation plan

**Status: designed, not implemented.** No evaluation harness ships in this cycle. This
document exists so the next cycle builds against a defined target rather than inventing one,
and so nothing in the current build is mistaken for a validated result.

Nothing in Market Radar today has been shown to predict anything. What *has* been verified is
that the deterministic machinery behaves as specified — see §5.

---

## 1. What must be measured

| Dimension | Question | Metric |
| --- | --- | --- |
| Retrieval quality | Did we find the relevant evidence? | recall against a labelled document set |
| Source quality | Did primary sources dominate? | primary-source ratio per finding |
| Evidence grounding | Is every claim supported? | share of report lines whose evidence ids resolve, and whose excerpt actually contains the claim |
| Independence accuracy | Did clustering group correctly? | precision/recall of cluster assignment against labelled ancestry |
| Contradiction coverage | Did we actively search against the thesis? | share of runs with ≥1 contradicting finding backed by ≥2 independent sources |
| Hallucination | Did the system invent anything? | count of unsupported statements — **target zero, structurally** |
| Research completeness | Were all dimensions investigated? | dimensions with evidence ÷ dimensions planned |
| Ranking quality | Did high-ranked themes prove more valuable? | see §3 |

The hallucination target is structural rather than aspirational: reports are assembled from
stored findings and cannot contain a sentence without an evidence link (ADR-009). The metric
exists to *detect a regression* in that property — for instance, if a narrative-generation
layer is added later.

---

## 2. Golden dataset

Historical themes with known outcomes, each labelled with the signals available at the time,
the date evidence first supported them, the date mainstream awareness arrived, the fundamental
outcome and the market outcome:

AI infrastructure · cloud spending · memory cycles · EV supply chains · LNG · uranium ·
defence spending · data-centre power · semiconductor equipment.

Building this requires a historical document provider. It is the **hard prerequisite** for
every metric in §3, and it is a data-acquisition problem, not an engineering one — which is
why it is scheduled rather than stubbed.

---

## 3. Backtesting

Replay the pipeline as of a historical date, with only documents that existed then, and ask:
*could Market Radar have detected this earlier?*

Measured: discovery date, consensus date, **discovery lead time**, fundamental validation,
market reaction, false positives, false negatives.

The as-of mechanics already exist — every stage takes an explicit `as_of` and the windows are
computed from it, which is why the end-to-end test can run at a fixed instant. The missing
piece is historical documents.

**Do not optimise on stock returns.** A theme that was real and early but whose securities
did not move is a success for a research product and a failure only for a trading signal.

---

## 4. North star: validated early discoveries

> Themes discovered at maturity ≤ ACCELERATING that subsequently demonstrated meaningful
> fundamental or market development.

Tracked alongside: themes discovered, themes validated, false-positive rate, median discovery
lead time. This metric rewards quality over volume, which is why it is preferred to any count
of articles, reports or alerts.

---

## 5. What is verified today

138 automated tests pass against a real PostgreSQL database. What they establish is
*behavioural correctness of the deterministic machinery*, not predictive validity:

* ten copies of one announcement yield one independent confirmation, and adding duplicates
  never raises independence;
* a large but stable phenomenon produces near-zero acceleration, while a doubling produces
  high acceleration;
* an unavailable input is excluded and the weights renormalised — never defaulted to zero;
* a single source, however authoritative, cannot produce high confidence;
* stability statements produce no change events, and forward-looking statements produce
  evidence but no events;
* every report claim's evidence ids resolve to a document and a source;
* the pipeline is idempotent across re-runs.

Three defects were found *by* these tests during this cycle and fixed: additive confidence
letting source quality substitute for corroboration; per-bucket independence undercounting
sources across a window; and a near-duplicate threshold calibrated in the wrong direction.
That is the evaluation layer earning its place before it formally exists.

---

## 6. Calibration debt

Named explicitly so it is not forgotten:

| Parameter | Current value | Basis | Needs |
| --- | --- | --- | --- |
| Score model weights | PRD suggestions | none | backtest against outcomes |
| Saturation constant `K` | 2.5 | chosen for range behaviour | sensitivity analysis |
| Baseline floor | 8.0 | prevents division blow-up | real-corpus distribution |
| Duplicate threshold | 0.60 | swept on synthetic corpus (ADR-014) | labelled real documents |
| Maturity thresholds | judgement | none | outcome labelling |
| Source quality priors | PRD table | none | observed reliability over time |
| Formation gate (accel ≥ 20, ≥ 2 sources) | judgement | none | false-positive analysis |

Every one of these is a named constant or a configuration value in one place, so calibration
is a data problem rather than a refactor.
