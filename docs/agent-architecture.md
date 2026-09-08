# Agent architecture

## The contract

An agent is a **typed function with a budget and a trace**, not a chat session.

```python
class Agent(ABC, Generic[InputT, OutputT]):
    name: str
    version: str
    input_model: type[InputT]      # Pydantic, validated
    output_model: type[OutputT]    # Pydantic, validated

    def execute(self, ctx: AgentContext, payload: InputT) -> OutputT: ...
    def strategy(self, ctx) -> str          # 'rule_based' | 'llm' — recorded and displayed
    def model_name(self, ctx) -> str | None
    def prompt_version(self, ctx) -> str | None
```

`Agent.run()` wraps `execute()` and always writes an `agent_runs` row — on success, failure,
timeout or budget stop — carrying status, timings, strategy, model, prompt version, budget,
consumption, and both typed envelopes.

That is what makes the research trace in the UI a **record**: it is a read of `agent_runs`.
If no agent ran, the panel says so.

### Budgets and stopping rules

`AgentBudget(max_llm_calls, max_searches, max_seconds, max_output_items)`. `AgentContext`
meters spend; exceeding a limit raises `BudgetExceededError`, which the wrapper records as
status `BUDGET_EXCEEDED` rather than as a crash. The research loop additionally stops on:

* **plan completion** — every question executed;
* **saturation** — six consecutive searches returning no new document;
* **search-budget exhaustion**.

The stop reason is stored on the run and shown in the UI.

### Structured output only

Agents exchange Pydantic schemas, never prose (Master Build Prompt §22). An LLM plan is
validated against `PlanOutput` before it is allowed near the database; a malformed one raises
and the agent falls back rather than half-populating anything.

---

## Implemented: `ResearchPlannerAgent` v1.0.0

**Objective.** Given a detected theme, decide what must be researched.

**Input** (`PlanInput`): theme, hypothesis, subjects, signal summaries (level, acceleration,
independent sources), mapped companies with roles and exposure, contradiction ratio, maturity.

**Output** (`PlanOutput`): a hypothesis and typed `PlannedQuestion`s across 12 dimensions —
demand, supply, pricing, capacity, customers, competition, technology, company exposure,
market expectations, risks, contradictory evidence, historical analogue.

**Two strategies, one schema:**

| Strategy | When | Notes |
| --- | --- | --- |
| `RuleBasedPlanStrategy` | default; the only path when no API key is configured | Deterministic. Questions are parameterised by the theme's actual subjects, accelerating signals and mapped companies. |
| `LlmPlanStrategy` | only when `MARKETRADAR_ANTHROPIC_API_KEY` is set | Output validated against the same schema; a failure degrades to the rule-based path and the fallback is logged and traced. |

**Structural guarantee.** `_enforce_coverage` requires every plan — whichever strategy
produced it — to cover demand, supply, pricing, company exposure, **contradictory evidence**
and **historical analogue**. A plan missing any of them is supplemented deterministically and
the supplementation is recorded in the rationale. Searching only for confirmation is the most
damaging failure mode in this product, so it is prevented structurally rather than requested
politely in a prompt.

**Honesty.** The strategy is stored on the plan and the agent run, and the UI prints "no
language model was used; this plan was generated deterministically" when that is the case.

---

## Prompt-injection posture

Retrieved documents are untrusted input (Master Build Prompt §55). Separation is enforced by
construction:

* Source content never occupies the system role.
* Any retrieved content passed to a model is wrapped in a delimited
  `<untrusted_source_content>` block whose framing states that directives inside it are data
  to be reported, not followed.
* The pipeline's *deterministic* stages — extraction, clustering, scoring, exposure — read
  document text with regular expressions and set arithmetic. There is no instruction channel
  to hijack, which is the strongest form of this defence and a further reason the
  deterministic-first ordering matters.
* Outbound fetches pass an SSRF allowlist (`https` + host allowlist) before a connection
  opens, so a document cannot steer the system into fetching an internal address.

---

## Specified, not implemented

The Master Build Prompt names sixteen agents. This cycle ships one. The rest are absent from
the code and named as absent in the UI:

`DiscoveryAgent`, `SearchAgent`, `NewsAgent`, `FilingAgent`, `IndustryAgent`, `MarketAgent`,
`SupplyChainAgent`, `CompanyResearchAgent`, `BullAgent`, `BearAgent`, `SkepticAgent`,
`HistoricalAnalogueAgent`, `ValuationAgent`, `CatalystAgent`, `RiskAgent`,
`InvestmentCommitteeAgent`.

**Recommended order for the next cycle**, chosen by what the current output most obviously
lacks rather than by the list's order:

1. **BearAgent** — the contradiction section currently aggregates counter-evidence the
   deterministic pipeline happened to find. An agent that actively hunts disconfirmation is
   the largest single quality gain available, and it needs no new data provider.
2. **SkepticAgent** — audits a completed run for double-counted evidence, causal errors and
   thin samples. It has real material to audit precisely because ancestry and independence
   are already measured.
3. **BullAgent** — symmetry, once the adversary exists. Building it first would optimise for
   confirmation.
4. **InvestmentCommitteeAgent** — synthesis over the three, summarising disagreement rather
   than averaging it.

`ValuationAgent` and `CatalystAgent` are blocked on data, not on code: without a market-data
provider they could only produce prose, which is the failure mode this architecture exists to
avoid.

## Model routing (designed, unexercised)

Cheap models for classification, tagging and deduplication; mid-tier for summarisation and
query generation; premium reasoning for hypothesis construction, debate and synthesis. The
seam exists (`agents/llm.py`, per-agent `model_name()`), but with no key configured in this
environment nothing has exercised it, and it is not claimed as working.
