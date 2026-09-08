"""Typed agent input/output schemas.

Agents exchange these, never prose. Prose can be rendered *from* a schema later; a schema
cannot be recovered from prose, and an unvalidated sentence is exactly where an unsupported
claim enters a research system (Master Build Prompt §22).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from marketradar.domain.enums import Direction, ResearchDimension


class SignalSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    level: float = Field(ge=0, le=100)
    acceleration: float = Field(ge=-100, le=100)
    direction: Direction
    independent_sources: int


class CompanySummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    name: str
    ticker: str | None
    role: str
    order_of_effect: int
    exposure_score: float


class PlanInput(BaseModel):
    """What the planner is told about a theme."""

    model_config = ConfigDict(frozen=True)

    theme_slug: str
    theme_name: str
    hypothesis: str
    subjects: tuple[str, ...]
    signals: tuple[SignalSummary, ...]
    companies: tuple[CompanySummary, ...]
    contradiction_ratio: float
    maturity: str


class PlannedQuestion(BaseModel):
    """One research question with the terms used to search for its answer."""

    model_config = ConfigDict(frozen=True)

    dimension: ResearchDimension
    question: str = Field(min_length=8, max_length=400)
    priority: int = Field(ge=1, le=5)
    seeks_counter_evidence: bool = False
    search_terms: tuple[str, ...] = Field(min_length=1)


class PlanOutput(BaseModel):
    """A research plan: what must be investigated, and why."""

    hypothesis: str
    questions: tuple[PlannedQuestion, ...] = Field(min_length=1)
    rationale: str

    @property
    def counter_evidence_questions(self) -> tuple[PlannedQuestion, ...]:
        return tuple(q for q in self.questions if q.seeks_counter_evidence)

    def covered_dimensions(self) -> set[ResearchDimension]:
        return {q.dimension for q in self.questions}
