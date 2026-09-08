"""Research Planner: structural guarantees that survive whichever strategy runs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from marketradar.agents.planner import (
    REQUIRED_DIMENSIONS,
    ResearchPlannerAgent,
    RuleBasedPlanStrategy,
)
from marketradar.agents.schemas import (
    CompanySummary,
    PlanInput,
    PlannedQuestion,
    PlanOutput,
    SignalSummary,
)
from marketradar.domain.enums import Direction, ResearchDimension


@pytest.fixture
def plan_input():
    return PlanInput(
        theme_slug="ai-memory-demand",
        theme_name="AI Memory Demand",
        hypothesis="AI infrastructure growth is increasing memory demand faster than supply.",
        subjects=("memory", "ai_infrastructure"),
        signals=(
            SignalSummary(
                key="memory_demand",
                name="Memory demand",
                level=67.0,
                acceleration=91.0,
                direction=Direction.POSITIVE,
                independent_sources=6,
            ),
        ),
        companies=(
            CompanySummary(
                key="nbmx",
                name="Northbridge Memory Corp",
                ticker="NBMX",
                role="DIRECT_BENEFICIARY",
                order_of_effect=1,
                exposure_score=81.0,
            ),
        ),
        contradiction_ratio=0.1,
        maturity="ACCELERATING",
    )


def test_rule_based_plan_covers_every_required_dimension(plan_input):
    plan = RuleBasedPlanStrategy().build(plan_input)
    assert plan.covered_dimensions() >= REQUIRED_DIMENSIONS


def test_a_plan_always_hunts_for_counter_evidence(plan_input):
    """Searching only for confirmation is the most damaging failure mode here."""
    plan = RuleBasedPlanStrategy().build(plan_input)
    assert len(plan.counter_evidence_questions) >= 2
    assert ResearchDimension.CONTRADICTORY_EVIDENCE in plan.covered_dimensions()
    assert ResearchDimension.HISTORICAL_ANALOGUE in plan.covered_dimensions()


def test_questions_are_parameterised_by_the_actual_theme(plan_input):
    plan = RuleBasedPlanStrategy().build(plan_input)
    text = " ".join(q.question for q in plan.questions)
    assert "memory" in text
    assert "Northbridge Memory Corp" in text


def test_accelerating_signals_generate_their_own_question(plan_input):
    plan = RuleBasedPlanStrategy().build(plan_input)
    assert any("Memory demand" in q.question and "baseline" in q.question for q in plan.questions)


def test_plan_is_deterministic(plan_input):
    first = RuleBasedPlanStrategy().build(plan_input)
    second = RuleBasedPlanStrategy().build(plan_input)
    assert [q.question for q in first.questions] == [q.question for q in second.questions]


def test_incomplete_plans_are_supplemented_not_accepted(plan_input):
    """An LLM plan missing required dimensions is topped up deterministically."""
    agent = ResearchPlannerAgent(prefer_llm=False)
    thin = PlanOutput(
        hypothesis=plan_input.hypothesis,
        questions=(
            PlannedQuestion(
                dimension=ResearchDimension.DEMAND,
                question="Is demand rising in a durable way across end markets?",
                priority=1,
                search_terms=("demand",),
            ),
        ),
        rationale="A deliberately thin plan.",
    )
    completed = agent._enforce_coverage(thin, plan_input)
    assert completed.covered_dimensions() >= REQUIRED_DIMENSIONS
    assert len(completed.questions) > len(thin.questions)
    assert "Supplemented" in completed.rationale


def test_strategy_is_rule_based_when_no_llm_is_configured(plan_input):
    agent = ResearchPlannerAgent(prefer_llm=False)
    assert agent.strategy(None) == "rule_based"  # type: ignore[arg-type]
    assert agent.model_name(None) is None  # type: ignore[arg-type]


def test_planned_questions_reject_empty_search_terms():
    with pytest.raises(PydanticValidationError):
        PlannedQuestion(
            dimension=ResearchDimension.DEMAND,
            question="Is demand rising?",
            priority=1,
            search_terms=(),
        )
