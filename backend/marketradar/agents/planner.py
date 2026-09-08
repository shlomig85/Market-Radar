"""Research Planner agent.

Given a detected theme, decide what must be researched. Two interchangeable strategies sit
behind one output schema:

* :class:`RuleBasedPlanStrategy` — deterministic, offline, free. The default, and the only
  one active when no LLM key is configured.
* :class:`LlmPlanStrategy` — used only when a key is present; its output is validated
  against the same Pydantic schema before it is allowed anywhere near the database.

Which strategy produced a plan is recorded on the plan and the agent run and shown in the
UI. A rule-based plan is never presented as though a model reasoned about it (ADR-008).

Both strategies must satisfy the same structural guarantee: a plan **always** contains
counter-evidence and historical-analogue questions. Searching only for confirmation is the
single most damaging failure mode in this product, so it is prevented structurally rather
than requested politely.
"""

from __future__ import annotations

from marketradar.agents.base import Agent, AgentContext
from marketradar.agents.llm import PROMPT_VERSION, available, complete_json
from marketradar.agents.schemas import PlanInput, PlannedQuestion, PlanOutput
from marketradar.config import get_settings
from marketradar.domain.enums import ResearchDimension
from marketradar.errors import ValidationError
from marketradar.logging import get_logger

log = get_logger(__name__)

PLANNER_VERSION = "1.0.0"

#: Dimensions every plan must cover, whatever produced it.
REQUIRED_DIMENSIONS = frozenset(
    {
        ResearchDimension.DEMAND,
        ResearchDimension.SUPPLY,
        ResearchDimension.PRICING,
        ResearchDimension.COMPANY_EXPOSURE,
        ResearchDimension.CONTRADICTORY_EVIDENCE,
        ResearchDimension.HISTORICAL_ANALOGUE,
    }
)


class RuleBasedPlanStrategy:
    """Deterministic plan generation from the theme's own measurements.

    The questions are templated, but they are not generic: they are parameterised by the
    subjects that actually formed the theme, the signals that actually accelerated, and the
    companies the value chain actually reached. A plan for a different theme asks about
    different things because those inputs differ.
    """

    name = "rule_based"
    version = PLANNER_VERSION

    def build(self, payload: PlanInput) -> PlanOutput:
        subjects = [s.replace("_", " ") for s in payload.subjects] or ["the theme"]
        primary = subjects[0]
        accelerating = [s for s in payload.signals if s.acceleration >= 20]
        top_companies = payload.companies[:3]
        company_clause = (
            ", ".join(f"{c.name} ({c.ticker})" if c.ticker else c.name for c in top_companies)
            or "the mapped companies"
        )

        questions: list[PlannedQuestion] = [
            PlannedQuestion(
                dimension=ResearchDimension.DEMAND,
                question=f"Is end demand for {primary} increasing, and is the increase "
                "attributable to a structural driver rather than restocking?",
                priority=1,
                search_terms=(f"{primary} demand", f"{primary} order backlog", "demand growth"),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.SUPPLY,
                question=f"Is {primary} supply constrained, and how quickly can capacity "
                "respond?",
                priority=1,
                search_terms=(
                    f"{primary} capacity",
                    f"{primary} shortage",
                    "inventory lead times",
                ),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.PRICING,
                question=f"Is {primary} pricing rising, and is the move contract-based or "
                "spot-only?",
                priority=1,
                search_terms=(f"{primary} pricing", "contract pricing", "price increase"),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.CAPACITY,
                question=f"How much new {primary} capacity has been announced, and when does "
                "it come online?",
                priority=2,
                search_terms=("capacity expansion", "capital expenditure", "new facility"),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.CUSTOMERS,
                question=f"Which customers are driving {primary} demand, and are they "
                "committing to forward purchases?",
                priority=2,
                search_terms=("customers", "purchase commitments", "procurement"),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.COMPANY_EXPOSURE,
                question=f"Which public companies have the greatest incremental exposure to "
                f"this change, and through what mechanism? Candidates: {company_clause}.",
                priority=1,
                search_terms=tuple(c.name for c in top_companies) or (primary,),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.COMPETITION,
                question=f"Are competitors expanding into {primary}, and does that erode the "
                "advantage of the exposed companies?",
                priority=3,
                search_terms=("competition", "market share", "capacity additions"),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.TECHNOLOGY,
                question=f"Could a technology change reduce the {primary} requirement per "
                "unit of end demand?",
                priority=3,
                search_terms=("technology", "efficiency", "next generation"),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.MARKET_EXPECTATIONS,
                question="What does the available evidence say about how much of this change "
                "is already reflected in market expectations?",
                priority=2,
                search_terms=("valuation", "shares", "analysts", "expectations"),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.RISKS,
                question=f"What would have to be true for the {primary} thesis to be wrong "
                "within twelve months?",
                priority=2,
                seeks_counter_evidence=True,
                search_terms=("risk", "oversupply", "slowdown", "decline"),
            ),
            # Mandatory: the plan must actively hunt for disconfirmation.
            PlannedQuestion(
                dimension=ResearchDimension.CONTRADICTORY_EVIDENCE,
                question=f"What direct evidence contradicts the claim that {primary} demand "
                "is outrunning supply?",
                priority=1,
                seeks_counter_evidence=True,
                search_terms=(
                    f"{primary} oversupply",
                    "demand slowdown",
                    "pricing decline",
                    "inventory build",
                ),
            ),
            PlannedQuestion(
                dimension=ResearchDimension.HISTORICAL_ANALOGUE,
                question=f"When has a comparable {primary} cycle occurred before, and how did "
                "it resolve for suppliers and for pricing?",
                priority=2,
                seeks_counter_evidence=True,
                search_terms=("historical cycle", "previous cycle", "cycle history"),
            ),
        ]

        for signal in accelerating:
            questions.append(
                PlannedQuestion(
                    dimension=ResearchDimension.DEMAND
                    if "demand" in signal.key
                    else ResearchDimension.SUPPLY,
                    question=f"The '{signal.name}' signal is running {signal.acceleration:.0f} "
                    f"against its own baseline on {signal.independent_sources} independent "
                    "sources. What explains the change, and is it durable?",
                    priority=2,
                    search_terms=(signal.name, primary),
                )
            )

        rationale = (
            f"Plan generated deterministically from the theme's measurements: "
            f"{len(payload.signals)} signals ({len(accelerating)} accelerating), "
            f"{len(payload.companies)} mapped companies, contradiction ratio "
            f"{payload.contradiction_ratio:.0%}, maturity {payload.maturity}. "
            "Counter-evidence and historical-analogue questions are included by construction, "
            "not by judgement."
        )
        return PlanOutput(
            hypothesis=payload.hypothesis, questions=tuple(questions), rationale=rationale
        )


class LlmPlanStrategy:
    """LLM-generated plan, validated against the same schema before use."""

    name = "llm"
    version = PLANNER_VERSION

    SYSTEM_PROMPT = (
        "You are a research planner for an investment intelligence system. Given a detected "
        "theme and its measurements, produce research questions that would establish whether "
        "the underlying change is real, durable and economically meaningful.\n\n"
        "Rules:\n"
        "- Return ONLY a JSON object matching the requested schema.\n"
        "- Include questions that seek evidence AGAINST the hypothesis, and at least one "
        "historical-analogue question.\n"
        "- Do not assert facts. Ask questions.\n"
        "- Do not name price targets or make recommendations."
    )

    def build(self, payload: PlanInput, ctx: AgentContext) -> PlanOutput:
        dimensions = ", ".join(d.value for d in ResearchDimension)
        user_prompt = (
            f"Theme: {payload.theme_name}\n"
            f"Hypothesis: {payload.hypothesis}\n"
            f"Subjects: {', '.join(payload.subjects)}\n"
            f"Maturity: {payload.maturity}\n"
            f"Contradiction ratio: {payload.contradiction_ratio:.2f}\n"
            "Signals:\n"
            + "\n".join(
                f"  - {s.name}: level {s.level:.0f}, acceleration {s.acceleration:.0f}, "
                f"{s.independent_sources} independent sources"
                for s in payload.signals
            )
            + "\nCompanies:\n"
            + "\n".join(
                f"  - {c.name} ({c.ticker or 'n/a'}): {c.role}, order {c.order_of_effect}, "
                f"exposure {c.exposure_score:.0f}"
                for c in payload.companies
            )
            + f"\n\nValid dimensions: {dimensions}\n\n"
            'Return JSON: {"hypothesis": str, "rationale": str, "questions": '
            '[{"dimension": str, "question": str, "priority": 1-5, '
            '"seeks_counter_evidence": bool, "search_terms": [str, ...]}]}'
        )
        ctx.spend_llm_call()
        payload_json, response = complete_json(self.SYSTEM_PROMPT, user_prompt)
        ctx.consumption.input_tokens += response.input_tokens
        ctx.consumption.output_tokens += response.output_tokens
        # Pydantic validation is the gate: a malformed or hallucinated-shape plan raises here
        # and the agent wrapper records the failure rather than persisting garbage.
        return PlanOutput.model_validate(payload_json)


class ResearchPlannerAgent(Agent[PlanInput, PlanOutput]):
    """Theme -> research plan."""

    name = "research_planner"
    version = PLANNER_VERSION
    input_model = PlanInput
    output_model = PlanOutput

    def __init__(self, prefer_llm: bool = True) -> None:
        self._prefer_llm = prefer_llm
        self._rule_based = RuleBasedPlanStrategy()
        self._llm = LlmPlanStrategy()

    def _use_llm(self) -> bool:
        return self._prefer_llm and available(get_settings())

    def strategy(self, ctx: AgentContext) -> str:
        return self._llm.name if self._use_llm() else self._rule_based.name

    def model_name(self, ctx: AgentContext) -> str | None:
        return get_settings().planner_model if self._use_llm() else None

    def prompt_version(self, ctx: AgentContext) -> str | None:
        return PROMPT_VERSION if self._use_llm() else None

    def execute(self, ctx: AgentContext, payload: PlanInput) -> PlanOutput:
        if self._use_llm():
            try:
                plan = self._llm.build(payload, ctx)
                return self._enforce_coverage(plan, payload)
            except Exception as exc:  # noqa: BLE001
                # A failed model call degrades to the deterministic planner rather than
                # failing the research run. The fallback is logged and shows up in the trace.
                log.warning("planner.llm_failed_falling_back", error=str(exc))
        return self._enforce_coverage(self._rule_based.build(payload), payload)

    def _enforce_coverage(self, plan: PlanOutput, payload: PlanInput) -> PlanOutput:
        """Guarantee the structural requirements, whatever produced the plan."""
        missing = REQUIRED_DIMENSIONS - plan.covered_dimensions()
        if not missing:
            return plan
        if not plan.counter_evidence_questions and (
            ResearchDimension.CONTRADICTORY_EVIDENCE in missing
        ):
            log.warning("planner.counter_evidence_missing", theme=payload.theme_slug)

        fallback = self._rule_based.build(payload)
        supplements = tuple(q for q in fallback.questions if q.dimension in missing)
        if not supplements:
            raise ValidationError(
                "Plan is missing required dimensions and no supplement is available",
                missing=[d.value for d in missing],
            )
        return PlanOutput(
            hypothesis=plan.hypothesis,
            questions=plan.questions + supplements,
            rationale=(
                f"{plan.rationale} Supplemented with {len(supplements)} deterministic "
                f"question(s) to cover required dimensions: "
                f"{', '.join(sorted(d.value for d in missing))}."
            ),
        )
