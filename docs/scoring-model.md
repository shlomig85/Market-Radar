# Scoring model

Every score in Market Radar satisfies four properties:

1. **Versioned** — weights are data with a version id, stored on every score computed.
2. **Decomposable** — each component's input, normalisation, weight and contribution is
   stored and returned by the API.
3. **Honest about gaps** — an unavailable input is excluded and the remaining weights
   renormalised; it never becomes `0` or a neutral default.
4. **Not a price forecast** — these rank *research priority*, and confidence is reported
   separately from opportunity.

---

## The three models (`marketradar/scoring/models.py`)

### `trend_score` v1.0.0 — how strong, and how *changing*

| Component | Weight | Status in this build |
| --- | --- | --- |
| Signal strength | 0.20 | computed |
| Signal acceleration | 0.15 | computed |
| Evidence diversity | 0.15 | computed |
| Economic impact | 0.10 | **unavailable** — no fundamental data |
| Novelty | 0.10 | computed (coverage proxy) |
| Market mispricing potential | 0.10 | **unavailable** — no market data |
| Company exposure | 0.10 | computed |
| Catalyst proximity | 0.05 | **unavailable** — no catalyst engine |
| Confidence | 0.05 | computed |

Weight coverage in this build: **0.75**.

### `opportunity_score` v1.0.0 — research-opportunity quality

Trend strength 0.25, acceleration 0.15, company exposure 0.15, market mispricing 0.15,
evidence quality 0.10, catalyst strength 0.10, risk/reward 0.05, novelty 0.05. Mispricing,
catalysts and risk/reward are unavailable; coverage **0.70**.

### `confidence_score` v1.0.0 — how well established the conclusion is

Independent sources 0.30, source quality 0.20, primary ratio 0.20, source diversity 0.15,
contradiction balance 0.15. All components computable; coverage **1.00**.

---

## Underlying calculations

### Signal strength

```
per-event contribution  = magnitude × confidence × (source_quality / 100)
per-cluster             = the single strongest contribution in that cluster
net                     = Σ over clusters of (signal's sign for the event type) × contribution
strength                = 100 × (1 − e^(−|net| / 2.5))
```

Aggregating **per cluster** is the anti-amplification step. Saturating means a large pile of
evidence cannot dominate a comparison by volume alone.

### Acceleration

```
denominator  = max(baseline, 8.0)          # floor stops a quiet history exploding
ratio        = (observation − baseline) / denominator
acceleration = 100 × tanh(ratio)           # bounded to −100..100, monotonic
```

Level and acceleration are computed differently on purpose. **Level** aggregates the whole
window at once, so quiet weeks (a reporting-cadence artefact) do not depress it.
**Acceleration** compares mean weekly buckets, because windows of 30 and 90 days are only
comparable as rates.

### Confidence, and why it is multiplicative

```
gate    = 1 − e^(−independent_sources / 2.5)
quality = 0.30·diversity + 0.35·primary_ratio + 0.35·(avg_quality/100)
value   = 100 × gate × (0.45 + 0.55 × quality)
```

Structured as `independence × quality`, not `independence + quality`. Source quality and
primacy describe how good evidence is *if* it is right; they cannot substitute for
corroboration. An earlier additive version scored a single regulatory filing at 51/100 —
this was caught by a unit test asserting the documented intent, and the same gating is
applied to each quality component of `confidence_score` so the decomposition still explains
itself.

A single independent source now scores ~27/100 however authoritative it is.

### Novelty (a proxy, and labelled as one)

```
penalty = 0.65 × mainstream_coverage_share + (0.35 if price movement is reported)
novelty = 100 × max(0, 1 − penalty)
```

This measures *coverage mix*, not positioning, estimate revisions or valuation — none of
which are available without a market-data provider. Every place it is consumed says so.

### Exposure

Multiplicative along the value-chain path: `Π(edge_weight × role_multiplier)` for the score,
`Π(edge_confidence) × 0.85^hops` for confidence. Role multipliers: direct beneficiary 1.0,
supplier 0.8, competitor 0.6, infrastructure 0.6, customer 0.5, substitute 0.5.

### Maturity

Ordered rules over measurable inputs (`marketradar/trends/maturity.py`), each returning a
rationale string that quotes the threshold it applied. Thresholds: 2 independent sources to
clear `INVISIBLE`, 4 for `DEVELOPING`, 5 plus acceleration ≥ 25 for `ACCELERATING`,
mainstream share ≥ 0.45 for `CONSENSUS`, ≥ 0.65 with reported price movement for `CROWDED`,
contradiction ≥ 0.60 (on ≥ 4 sources) for `INVALIDATED`.

---

## Calibration status

**These weights are the PRD's suggested starting points. They are not empirically
calibrated**, and nothing in this build claims they predict anything. `docs/evaluation-plan.md`
describes the backtest that would earn them that status. Until then, the useful properties
are transparency and reproducibility, not accuracy.

## Changing a score model

Bump `version`, do not edit a released weight set in place. Every score row stores the
version that produced it, so historical figures remain interpretable after the model moves
on. Tests assert that every model's weights sum to 1.0 and that a mis-specified model cannot
be constructed.
