"""The research loop.

    theme -> plan -> search -> sources -> evidence -> structured findings -> report

Every step writes a durable row, so the run can be replayed after the fact: the questions
asked, the exact queries issued, which provider answered and in what mode, which documents
were new, which evidence rows each finding rests on.

Stopping rules are explicit (Master Build Prompt §56): the loop halts on plan completion, on
search-budget exhaustion, or on saturation — consecutive searches returning nothing new.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.agents.base import AgentBudget, AgentContext
from marketradar.agents.planner import ResearchPlannerAgent
from marketradar.agents.schemas import CompanySummary, PlanInput, SignalSummary
from marketradar.bus import DomainEvent, get_bus
from marketradar.config import Settings, get_settings
from marketradar.domain.enums import (
    DataMode,
    ProviderCapability,
    ResearchDimension,
    RunStatus,
)
from marketradar.domain.models import (
    Company,
    FindingEvidence,
    Hypothesis,
    ResearchFinding,
    ResearchPlan,
    ResearchQuestion,
    ResearchRun,
    SearchRun,
    Signal,
    Theme,
    ThemeCompanyExposure,
    ThemeSignal,
    Trend,
)
from marketradar.errors import NotFoundError
from marketradar.ingestion.pipeline import (
    PIPELINE_VERSION,
    extract_evidence,
    ingest_documents,
    rebuild_clusters,
    upsert_sources,
)
from marketradar.logging import bind_context, get_logger
from marketradar.providers import ProviderRegistry, build_default_registry
from marketradar.providers.base import ProviderQuery
from marketradar.research.findings import (
    DIMENSION_EVENT_TYPES,
    build_exposure_finding,
    build_finding,
    gather_evidence,
)
from marketradar.research.report import assemble_report
from marketradar.themes.definitions import THEME_TEMPLATES

log = get_logger(__name__)

#: Consecutive searches returning no new document before the loop declares saturation.
SATURATION_LIMIT = 6

#: Event types that, for the theme's core hypothesis, count as supporting it.
SUPPORTING_TYPES = frozenset(
    DIMENSION_EVENT_TYPES[ResearchDimension.DEMAND]
    | {
        event_type
        for event_type in DIMENSION_EVENT_TYPES[ResearchDimension.SUPPLY]
        if "CONSTRAINT" in event_type.value or "DECLINE" in event_type.value
    }
    | {event_type for event_type in DIMENSION_EVENT_TYPES[ResearchDimension.PRICING]
       if "INCREASE" in event_type.value}
)


@dataclass
class ResearchOutcome:
    research_run_id: str
    status: RunStatus
    plan_strategy: str
    question_count: int = 0
    search_count: int = 0
    new_documents: int = 0
    finding_count: int = 0
    contradicting_count: int = 0
    stop_reason: str = ""
    report_id: str | None = None
    notes: list[str] = field(default_factory=list)


def run_research(
    session: Session,
    theme_slug: str,
    as_of: datetime | None = None,
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    budget: AgentBudget | None = None,
) -> ResearchOutcome:
    """Run one research investigation of a theme and assemble its report."""
    settings = settings or get_settings()
    registry = registry or build_default_registry(settings)
    as_of = as_of or datetime.now(tz=UTC)
    budget = budget or AgentBudget()

    theme = session.scalar(select(Theme).where(Theme.slug == theme_slug))
    if theme is None:
        raise NotFoundError(f"No theme with slug '{theme_slug}'", slug=theme_slug)

    window_start = as_of - timedelta(days=settings.observation_window_days)
    subjects = set((theme.anchor_entities or {}).get("subjects", []))

    run = ResearchRun(
        theme_id=theme.id,
        status=RunStatus.RUNNING,
        started_at=as_of,
        budget=budget.to_dict(),
        data_mode=theme.data_mode,
        pipeline_version=PIPELINE_VERSION,
    )
    session.add(run)
    session.flush()
    bind_context(research_run_id=run.id)
    get_bus().publish(DomainEvent.RESEARCH_REQUESTED, {"theme": theme_slug, "run": run.id})

    outcome = ResearchOutcome(
        research_run_id=run.id, status=RunStatus.RUNNING, plan_strategy="unknown"
    )

    # --- 1. hypothesis ------------------------------------------------
    hypothesis = _ensure_hypothesis(session, theme)
    run.hypothesis_id = hypothesis.id

    # --- 2. plan ------------------------------------------------------
    ctx = AgentContext(session=session, research_run_id=run.id, budget=budget, as_of=as_of)
    plan_input = _build_plan_input(session, theme, hypothesis, as_of)
    agent = ResearchPlannerAgent()
    agent_result = agent.run(ctx, plan_input, data_mode=theme.data_mode)
    outcome.plan_strategy = agent_result.strategy

    if not agent_result.succeeded or agent_result.output is None:
        run.status = RunStatus.FAILED
        run.finished_at = datetime.now(tz=UTC)
        run.error = agent_result.error or "Planner produced no plan"
        run.stop_reason = "planner_failed"
        session.flush()
        outcome.status = RunStatus.FAILED
        outcome.stop_reason = "planner_failed"
        return outcome

    plan_output = agent_result.output
    plan = ResearchPlan(
        research_run_id=run.id,
        agent_run_id=agent_result.agent_run_id,
        hypothesis_statement=plan_output.hypothesis,
        strategy=agent_result.strategy,
        planner_version=agent.version,
        model=agent.model_name(ctx),
        prompt_version=agent.prompt_version(ctx),
        rationale=plan_output.rationale,
        data_mode=theme.data_mode,
    )
    session.add(plan)
    session.flush()

    questions: list[ResearchQuestion] = []
    for planned in plan_output.questions:
        row = ResearchQuestion(
            plan_id=plan.id,
            dimension=planned.dimension,
            question=planned.question,
            priority=planned.priority,
            seeks_counter_evidence=planned.seeks_counter_evidence,
            search_terms={"terms": list(planned.search_terms)},
        )
        session.add(row)
        questions.append(row)
    session.flush()
    outcome.question_count = len(questions)

    # --- 3. search ----------------------------------------------------
    stop_reason = "plan_complete"
    consecutive_empty = 0
    sources_by_capability = {}
    for capability in (ProviderCapability.NEWS_SEARCH, ProviderCapability.FILINGS):
        provider = registry.get(capability)
        if provider.health().available:
            sources_by_capability[capability] = upsert_sources(session, provider.sources())

    for question in sorted(questions, key=lambda q: (q.priority, q.dimension.value)):
        if consecutive_empty >= SATURATION_LIMIT:
            stop_reason = "saturation"
            break
        terms = tuple((question.search_terms or {}).get("terms", []))
        query = ProviderQuery(
            text=" ".join(terms),
            terms=terms,
            since=window_start - timedelta(days=settings.baseline_window_days),
            until=as_of,
            limit=20,
        )
        for capability, sources in sources_by_capability.items():
            provider = registry.get(capability)
            try:
                ctx.spend_search()
            except Exception:  # noqa: BLE001 - budget stop is expected, not exceptional
                stop_reason = "search_budget_exhausted"
                break

            started = time.monotonic()
            try:
                result = provider.search(query)
                report = ingest_documents(session, result, sources, now=as_of)
                status = RunStatus.SUCCEEDED
                error = None
            except Exception as exc:  # noqa: BLE001 - one provider failing must not end the run
                result = None
                report = None
                status = RunStatus.FAILED
                error = f"{type(exc).__name__}: {exc}"
                log.warning("research.search_failed", provider=provider.key, error=error)

            latency = int((time.monotonic() - started) * 1000)
            session.add(
                SearchRun(
                    research_run_id=run.id,
                    question_id=question.id,
                    provider_key=getattr(provider, "key", "unknown"),
                    provider_mode=result.mode if result else DataMode.UNAVAILABLE,
                    query=query.text,
                    executed_at=datetime.now(tz=UTC),
                    result_count=result.count if result else 0,
                    new_document_count=report.documents_created if report else 0,
                    latency_ms=latency,
                    status=status,
                    error=error,
                )
            )
            outcome.search_count += 1
            new_documents = report.documents_created if report else 0
            outcome.new_documents += new_documents
            consecutive_empty = 0 if new_documents else consecutive_empty + 1
        if stop_reason == "search_budget_exhausted":
            break
    session.flush()

    # --- 4. fold new documents into evidence --------------------------
    if outcome.new_documents:
        rebuild_clusters(session, settings)
        extract_evidence(session)
        rebuild_clusters(session, settings)

    # --- 5. findings --------------------------------------------------
    exposures = session.scalars(
        select(ThemeCompanyExposure).where(ThemeCompanyExposure.theme_id == theme.id)
    ).all()
    company_names = {
        c.id: f"{c.name} ({c.ticker})" if c.ticker else c.name
        for c in session.scalars(select(Company)).all()
    }

    # Evidence gathering keys on the dimension, so several questions about the same
    # dimension would yield identical findings. One finding per dimension, anchored to that
    # dimension's highest-priority question, keeps the report free of repetition that would
    # otherwise read as extra corroboration.
    by_dimension: dict[ResearchDimension, ResearchQuestion] = {}
    question_counts: dict[ResearchDimension, int] = {}
    for candidate in sorted(questions, key=lambda q: (q.priority, q.id)):
        question_counts[candidate.dimension] = question_counts.get(candidate.dimension, 0) + 1
        by_dimension.setdefault(candidate.dimension, candidate)

    for question in by_dimension.values():
        if question.dimension == ResearchDimension.COMPANY_EXPOSURE:
            draft = build_exposure_finding(list(exposures), company_names)
        else:
            evidence = gather_evidence(
                session, question.dimension, subjects, window_start, as_of
            )
            draft = build_finding(question.dimension, evidence, SUPPORTING_TYPES)

        finding = ResearchFinding(
            research_run_id=run.id,
            question_id=question.id,
            dimension=draft.dimension,
            claim=draft.claim,
            claim_type=draft.claim_type,
            stance=draft.stance,
            confidence=draft.confidence,
            independent_source_count=draft.independent_source_count,
            source_diversity=draft.source_diversity,
            reasoning_summary=(
                f"{draft.reasoning_summary} "
                f"Answers {question_counts.get(question.dimension, 1)} planned question(s) "
                f"in this dimension."
            ),
            generated_by="research_loop",
            data_mode=draft.data_mode,
        )
        session.add(finding)
        session.flush()
        for evidence_id in draft.evidence_ids:
            session.add(
                FindingEvidence(
                    finding_id=finding.id, evidence_id=evidence_id, role=draft.stance
                )
            )
        outcome.finding_count += 1
        if draft.stance.value == "CONTRADICTING":
            outcome.contradicting_count += 1
    session.flush()

    # --- 6. report ----------------------------------------------------
    run.status = RunStatus.SUCCEEDED
    run.finished_at = datetime.now(tz=UTC)
    run.stop_reason = stop_reason
    run.consumed = ctx.consumption.to_dict()
    session.flush()

    report_row = assemble_report(session, theme, run, as_of, registry)
    outcome.report_id = report_row.id
    outcome.status = RunStatus.SUCCEEDED
    outcome.stop_reason = stop_reason
    get_bus().publish(
        DomainEvent.RESEARCH_COMPLETED, {"theme": theme_slug, "run": run.id}
    )
    return outcome


def _ensure_hypothesis(session: Session, theme: Theme) -> Hypothesis:
    """Reuse the theme's open hypothesis, creating it from the template on first run."""
    existing = session.scalar(
        select(Hypothesis).where(Hypothesis.theme_id == theme.id, Hypothesis.status == "OPEN")
    )
    if existing is not None:
        return existing

    template = next((t for t in THEME_TEMPLATES if t.slug == theme.slug), None)
    statement = template.hypothesis if template else (
        f"{theme.name}: the measured change is real, durable and economically meaningful."
    )
    row = Hypothesis(
        theme_id=theme.id,
        statement=statement,
        rationale=theme.summary,
        status="OPEN",
        created_by="research_loop",
        data_mode=theme.data_mode,
    )
    session.add(row)
    session.flush()
    return row


def _build_plan_input(
    session: Session, theme: Theme, hypothesis: Hypothesis, as_of: datetime
) -> PlanInput:
    """Assemble the typed input the planner sees. Nothing else reaches it."""
    links = session.scalars(
        select(ThemeSignal).where(ThemeSignal.theme_id == theme.id)
    ).all()
    signal_ids = [link.signal_id for link in links]
    signals = (
        session.scalars(select(Signal).where(Signal.id.in_(signal_ids))).all()
        if signal_ids
        else []
    )
    trends = {
        trend.signal_id: trend
        for trend in session.scalars(
            select(Trend).where(Trend.theme_id == theme.id)
        ).all()
    }

    summaries: list[SignalSummary] = []
    for signal in signals:
        trend = trends.get(signal.id)
        if trend is None:
            continue
        summaries.append(
            SignalSummary(
                key=signal.key,
                name=signal.name,
                level=max(0.0, min(100.0, trend.observation_strength)),
                acceleration=trend.acceleration,
                direction=trend.direction,
                independent_sources=int(
                    (trend.inputs or {}).get("observation_independent_sources", 0)
                ),
            )
        )

    exposures = session.scalars(
        select(ThemeCompanyExposure)
        .where(ThemeCompanyExposure.theme_id == theme.id)
        .order_by(ThemeCompanyExposure.exposure_score.desc())
    ).all()
    companies = {c.id: c for c in session.scalars(select(Company)).all()}
    company_summaries = [
        CompanySummary(
            key=companies[e.company_id].key,
            name=companies[e.company_id].name,
            ticker=companies[e.company_id].ticker,
            role=e.role.value,
            order_of_effect=e.order_of_effect,
            exposure_score=e.exposure_score,
        )
        for e in exposures
        if e.company_id in companies
    ]

    return PlanInput(
        theme_slug=theme.slug,
        theme_name=theme.name,
        hypothesis=hypothesis.statement,
        subjects=tuple(sorted((theme.anchor_entities or {}).get("subjects", []))),
        signals=tuple(summaries),
        companies=tuple(company_summaries),
        contradiction_ratio=0.0,
        maturity=theme.maturity.value,
    )
