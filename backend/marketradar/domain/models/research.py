"""Research: hypotheses, plans, searches, findings, agent traces and reports.

Everything an agent does leaves a durable row here. The "agent trace" the UI shows is this
table — if no agent ran, there is nothing to display, by construction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketradar.db.base import Base, TimestampMixin, UuidPkMixin
from marketradar.db.types import JsonDict, StrEnumText
from marketradar.domain.enums import (
    ClaimType,
    DataMode,
    FindingStance,
    ResearchDimension,
    RunStatus,
)


class Hypothesis(UuidPkMixin, TimestampMixin, Base):
    """A testable statement about a theme. Research is organised around these."""

    __tablename__ = "hypotheses"

    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.id"), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    __table_args__ = (Index("ix_hypotheses_theme_id", "theme_id"),)


class ResearchRun(UuidPkMixin, TimestampMixin, Base):
    """One orchestrated investigation of a hypothesis, under an explicit budget."""

    __tablename__ = "research_runs"

    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.id"), nullable=False)
    hypothesis_id: Mapped[str | None] = mapped_column(ForeignKey("hypotheses.id"))
    status: Mapped[RunStatus] = mapped_column(
        StrEnumText(RunStatus), nullable=False, default=RunStatus.PENDING
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget: Mapped[dict | None] = mapped_column(JsonDict)
    consumed: Mapped[dict | None] = mapped_column(JsonDict)
    #: Which stopping rule ended the run — budget, saturation, or plan completion.
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(32), nullable=False)

    plans: Mapped[list[ResearchPlan]] = relationship(back_populates="research_run")
    findings: Mapped[list[ResearchFinding]] = relationship(back_populates="research_run")
    search_runs: Mapped[list[SearchRun]] = relationship(back_populates="research_run")

    __table_args__ = (Index("ix_research_runs_theme_id", "theme_id"),)


class AgentRun(UuidPkMixin, TimestampMixin, Base):
    """A durable trace of one agent execution — success or failure.

    Reproducibility depends on this row: it records the model, prompt version, budget,
    consumption and the exact typed input/output envelopes.
    """

    __tablename__ = "agent_runs"

    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(32), nullable=False)
    research_run_id: Mapped[str | None] = mapped_column(ForeignKey("research_runs.id"))
    status: Mapped[RunStatus] = mapped_column(StrEnumText(RunStatus), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    #: 'rule_based' or 'llm'. Displayed in the UI: a deterministic plan is never shown as
    #: though a model reasoned about it.
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    input_payload: Mapped[dict | None] = mapped_column(JsonDict)
    output_payload: Mapped[dict | None] = mapped_column(JsonDict)
    budget: Mapped[dict | None] = mapped_column(JsonDict)
    consumed: Mapped[dict | None] = mapped_column(JsonDict)
    error: Mapped[str | None] = mapped_column(Text)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    __table_args__ = (
        Index("ix_agent_runs_research_run_id", "research_run_id"),
        Index("ix_agent_runs_started_at", "started_at"),
    )


class ResearchPlan(UuidPkMixin, TimestampMixin, Base):
    """The output of the Research Planner: what must be investigated, and why."""

    __tablename__ = "research_plans"

    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), nullable=False)
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    hypothesis_statement: Mapped[str] = mapped_column(Text, nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    planner_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    rationale: Mapped[str | None] = mapped_column(Text)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    research_run: Mapped[ResearchRun] = relationship(back_populates="plans")
    questions: Mapped[list[ResearchQuestion]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class ResearchQuestion(UuidPkMixin, TimestampMixin, Base):
    """One question in a research plan, bound to a dimension."""

    __tablename__ = "research_questions"

    plan_id: Mapped[str] = mapped_column(ForeignKey("research_plans.id"), nullable=False)
    dimension: Mapped[ResearchDimension] = mapped_column(
        StrEnumText(ResearchDimension), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    #: 1 (highest) .. 5. Drives execution order under a search budget.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    #: True for questions whose purpose is to find evidence AGAINST the hypothesis.
    seeks_counter_evidence: Mapped[bool] = mapped_column(nullable=False, default=False)
    search_terms: Mapped[dict | None] = mapped_column(JsonDict)

    plan: Mapped[ResearchPlan] = relationship(back_populates="questions")

    __table_args__ = (
        CheckConstraint("priority >= 1 AND priority <= 5", name="priority_range"),
        Index("ix_research_questions_plan_id", "plan_id"),
    )


class SearchRun(UuidPkMixin, TimestampMixin, Base):
    """One search executed against one provider, recorded verbatim for reproducibility."""

    __tablename__ = "search_runs"

    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), nullable=False)
    question_id: Mapped[str | None] = mapped_column(ForeignKey("research_questions.id"))
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[RunStatus] = mapped_column(StrEnumText(RunStatus), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    research_run: Mapped[ResearchRun] = relationship(back_populates="search_runs")

    __table_args__ = (Index("ix_search_runs_research_run_id", "research_run_id"),)


class ResearchFinding(UuidPkMixin, TimestampMixin, Base):
    """A structured answer to a research question, backed by evidence rows.

    A finding never carries an unsupported fact: its evidence links are the fact, and the
    ``claim_type`` states whether the sentence is a fact, an inference, a hypothesis or a
    forecast.
    """

    __tablename__ = "research_findings"

    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), nullable=False)
    question_id: Mapped[str | None] = mapped_column(ForeignKey("research_questions.id"))
    dimension: Mapped[ResearchDimension] = mapped_column(
        StrEnumText(ResearchDimension), nullable=False
    )
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[ClaimType] = mapped_column(StrEnumText(ClaimType), nullable=False)
    stance: Mapped[FindingStance] = mapped_column(StrEnumText(FindingStance), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    independent_source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_diversity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    research_run: Mapped[ResearchRun] = relationship(back_populates="findings")
    evidence_links: Mapped[list[FindingEvidence]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_findings_research_run_id", "research_run_id"),
        Index("ix_findings_stance", "stance"),
    )


class FindingEvidence(Base):
    """Association: the evidence supporting (or contradicting) a finding."""

    __tablename__ = "finding_evidence"

    finding_id: Mapped[str] = mapped_column(
        ForeignKey("research_findings.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_items.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[FindingStance] = mapped_column(StrEnumText(FindingStance), nullable=False)

    finding: Mapped[ResearchFinding] = relationship(back_populates="evidence_links")


class ResearchReport(UuidPkMixin, TimestampMixin, Base):
    """A report assembled deterministically from findings (ADR-009).

    ``sections`` holds the structured report body; every claim inside it carries evidence
    ids, so the API can resolve the full provenance chain for any line the UI renders.
    """

    __tablename__ = "research_reports"

    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), nullable=False)
    theme_id: Mapped[str] = mapped_column(ForeignKey("themes.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sections: Mapped[dict] = mapped_column(JsonDict, nullable=False)
    generator: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_version: Mapped[str] = mapped_column(String(32), nullable=False)
    opportunity_score_id: Mapped[str | None] = mapped_column(ForeignKey("scores.id"))
    confidence_score_id: Mapped[str | None] = mapped_column(ForeignKey("scores.id"))
    trend_score_id: Mapped[str | None] = mapped_column(ForeignKey("scores.id"))
    data_mode: Mapped[DataMode] = mapped_column(StrEnumText(DataMode), nullable=False)

    __table_args__ = (
        UniqueConstraint("research_run_id", name="uq_report_per_run"),
        Index("ix_reports_theme_id", "theme_id"),
    )
