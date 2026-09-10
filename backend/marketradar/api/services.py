"""Read services backing the API.

Query logic lives here rather than in routers so it can be tested without HTTP and reused
by the CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketradar.api.schemas import (
    AgentRunOut,
    EvidenceOut,
    ExposureOut,
    QuestionOut,
    ReportOut,
    ResearchTraceOut,
    ScoreComponentOut,
    ScoreOut,
    SearchRunOut,
    SubjectOut,
    ThemeDetailOut,
    ThemeSummaryOut,
    TrendingCompanyOut,
    TrendOut,
)
from marketradar.domain.models import (
    AgentRun,
    Company,
    Event,
    EventEvidence,
    EvidenceCluster,
    EvidenceItem,
    ResearchPlan,
    ResearchQuestion,
    ResearchReport,
    ResearchRun,
    Score,
    SearchRun,
    Signal,
    Source,
    SourceDocument,
    Subject,
    Theme,
    ThemeCompanyExposure,
    ThemeSignal,
    Trend,
)
from marketradar.errors import NotFoundError
from marketradar.evidence.independence import (
    PRIMARY_CLASSES,
    EvidenceDescriptor,
    profile,
)
from marketradar.scoring.company_trend import rate_companies


def _latest_scores(session: Session, theme_id: str) -> dict[str, Score]:
    rows = session.scalars(
        select(Score)
        .where(Score.subject_type == "THEME", Score.subject_id == theme_id)
        .order_by(Score.computed_at.desc())
    ).all()
    latest: dict[str, Score] = {}
    for score in rows:
        latest.setdefault(score.model_name, score)
    return latest


def _score_out(score: Score) -> ScoreOut:
    return ScoreOut(
        model_name=score.model_name,
        model_version=score.model_version,
        value=score.value,
        weight_coverage=score.weight_coverage,
        unavailable_components=(score.unavailable_components or {}).get("keys", []),
        computed_at=score.computed_at,
        data_mode=score.data_mode.value,
        notes=score.notes,
        components=[
            ScoreComponentOut(
                key=c.key,
                label=c.label,
                available=c.available,
                raw_input=c.raw_input,
                normalized=c.normalized,
                weight=c.weight,
                effective_weight=c.effective_weight,
                contribution=c.contribution,
                explanation=c.explanation,
            )
            for c in score.components
        ],
    )


def _theme_trends(session: Session, theme: Theme) -> list[tuple[Signal, Trend]]:
    links = session.scalars(select(ThemeSignal).where(ThemeSignal.theme_id == theme.id)).all()
    ids = [link.signal_id for link in links]
    if not ids:
        return []
    signals = {s.id: s for s in session.scalars(select(Signal).where(Signal.id.in_(ids))).all()}
    trends = session.scalars(
        select(Trend).where(Trend.theme_id == theme.id, Trend.signal_id.in_(ids))
    ).all()
    return [(signals[t.signal_id], t) for t in trends if t.signal_id in signals]


def _theme_evidence_descriptors(
    session: Session, theme: Theme
) -> list[EvidenceDescriptor]:
    """Independent-evidence descriptors for a theme's subjects in its observation window."""
    subjects = set((theme.anchor_entities or {}).get("subjects", []))
    trends = _theme_trends(session, theme)
    if not trends:
        return []
    window_start = min(trend.observation_start for _, trend in trends)
    window_end = max(trend.observation_end for _, trend in trends)

    rows = session.execute(
        select(Event, EvidenceItem, Source)
        .join(EventEvidence, EventEvidence.event_id == Event.id)
        .join(EvidenceItem, EvidenceItem.id == EventEvidence.evidence_id)
        .join(SourceDocument, SourceDocument.id == EvidenceItem.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
        .where(Event.occurred_at >= window_start, Event.occurred_at <= window_end)
    ).all()
    return [
        EvidenceDescriptor(
            evidence_id=item.id,
            cluster_id=event.cluster_id or item.id,
            source_class=source.source_class,
            source_quality=source.base_quality,
            is_primary=source.source_class in PRIMARY_CLASSES,
        )
        for event, item, source in rows
        if event.subject_key in subjects and event.cluster_id
    ]


def theme_summary(session: Session, theme: Theme) -> ThemeSummaryOut:
    scores = _latest_scores(session, theme.id)
    trends = _theme_trends(session, theme)
    company_count = session.scalar(
        select(func.count())
        .select_from(ThemeCompanyExposure)
        .where(ThemeCompanyExposure.theme_id == theme.id)
    ) or 0
    independence = profile(_theme_evidence_descriptors(session, theme))

    return ThemeSummaryOut(
        slug=theme.slug,
        name=theme.name,
        summary=theme.summary,
        maturity=theme.maturity.value,
        maturity_stage=theme.maturity.stage,
        market_awareness=theme.market_awareness.value,
        market_awareness_mode=theme.market_awareness_mode.value,
        data_mode=theme.data_mode.value,
        first_detected_at=theme.first_detected_at,
        last_updated_at=theme.last_updated_at,
        trend_score=scores["trend_score"].value if "trend_score" in scores else None,
        confidence_score=scores["confidence_score"].value if "confidence_score" in scores else None,
        opportunity_score=(
            scores["opportunity_score"].value if "opportunity_score" in scores else None
        ),
        top_acceleration=max((t.acceleration for _, t in trends), default=None),
        company_count=company_count,
        independent_source_count=independence.independent_source_count,
    )


def list_themes(session: Session) -> list[ThemeSummaryOut]:
    themes = session.scalars(select(Theme).order_by(Theme.last_updated_at.desc())).all()
    summaries = [theme_summary(session, theme) for theme in themes]
    # Ranked by research priority: opportunity weighted by confidence, exactly as the PRD's
    # ranking feed describes. Ties fall back to acceleration.
    return sorted(
        summaries,
        key=lambda s: (
            -( (s.opportunity_score or 0.0) * (s.confidence_score or 0.0) / 100.0 ),
            -(s.top_acceleration or 0.0),
        ),
    )


def get_theme(session: Session, slug: str) -> Theme:
    theme = session.scalar(select(Theme).where(Theme.slug == slug))
    if theme is None:
        raise NotFoundError(f"No theme with slug '{slug}'", slug=slug)
    return theme


def theme_detail(session: Session, slug: str) -> ThemeDetailOut:
    theme = get_theme(session, slug)
    scores = _latest_scores(session, theme.id)
    trends = _theme_trends(session, theme)
    descriptors = _theme_evidence_descriptors(session, theme)
    independence = profile(descriptors)

    exposures = session.scalars(
        select(ThemeCompanyExposure)
        .where(ThemeCompanyExposure.theme_id == theme.id)
        .order_by(ThemeCompanyExposure.exposure_score.desc())
    ).all()
    companies = {c.id: c for c in session.scalars(select(Company)).all()}

    contradiction = 0.0
    trend_score = scores.get("confidence_score")
    if trend_score is not None:
        for component in trend_score.components:
            if component.key == "contradiction_balance" and component.raw_input is not None:
                contradiction = component.raw_input

    return ThemeDetailOut(
        theme=theme_summary(session, theme),
        scores=[_score_out(s) for s in scores.values()],
        trends=[
            TrendOut(
                signal_key=signal.key,
                signal_name=signal.name,
                category=signal.category.value,
                observation_strength=trend.observation_strength,
                baseline_strength=trend.baseline_strength,
                acceleration=trend.acceleration,
                frequency_change=trend.frequency_change,
                independence_change=trend.independence_change,
                direction=trend.direction.value,
                observation_start=trend.observation_start,
                observation_end=trend.observation_end,
                baseline_start=trend.baseline_start,
                baseline_end=trend.baseline_end,
                data_mode=trend.data_mode.value,
            )
            for signal, trend in sorted(trends, key=lambda pair: -pair[1].acceleration)
        ],
        exposures=[
            ExposureOut(
                company_key=companies[e.company_id].key,
                company_name=companies[e.company_id].name,
                ticker=companies[e.company_id].ticker,
                is_fictional=companies[e.company_id].is_fictional,
                role=e.role.value,
                order_of_effect=e.order_of_effect,
                exposure_score=e.exposure_score,
                confidence=e.confidence,
                path=e.rationale,
                hops=(e.path or {}).get("hops", []),
                data_mode=e.data_mode.value,
            )
            for e in exposures
            if e.company_id in companies
        ],
        evidence_count=independence.evidence_count,
        independent_source_count=independence.independent_source_count,
        amplification_ratio=round(independence.amplification_ratio, 4),
        contradiction_ratio=round(contradiction, 4),
    )


def theme_evidence(session: Session, slug: str, limit: int = 200) -> list[EvidenceOut]:
    """Evidence behind a theme, each row carrying its full provenance chain."""
    theme = get_theme(session, slug)
    subjects = set((theme.anchor_entities or {}).get("subjects", []))

    rows = session.execute(
        select(EvidenceItem, SourceDocument, Source)
        .join(SourceDocument, SourceDocument.id == EvidenceItem.document_id)
        .join(Source, Source.id == SourceDocument.source_id)
        .order_by(EvidenceItem.event_at.desc())
    ).all()
    clusters = {c.id: c for c in session.scalars(select(EvidenceCluster)).all()}

    out: list[EvidenceOut] = []
    for item, document, source in rows:
        if item.subject_key not in subjects:
            continue
        cluster = clusters.get(item.cluster_id or "")
        out.append(
            EvidenceOut(
                id=item.id,
                claim=item.claim,
                excerpt=item.excerpt,
                confidence=item.confidence,
                event_type=item.event_type.value if item.event_type else None,
                direction=item.direction.value,
                subject_key=item.subject_key,
                rule_key=item.rule_key,
                extracted_by=item.extracted_by,
                extractor_version=item.extractor_version,
                data_mode=item.data_mode.value,
                event_at=item.event_at,
                published_at=item.published_at,
                retrieved_at=item.retrieved_at,
                event_at_inferred=document.event_at_inferred,
                document_id=document.id,
                document_title=document.title,
                document_url=document.url,
                source_key=source.key,
                source_name=source.name,
                publisher=source.publisher,
                source_type=source.source_type.value,
                source_class=source.source_class.value,
                source_quality=source.base_quality,
                is_synthetic_source=source.is_synthetic,
                cluster_id=item.cluster_id,
                cluster_method=document.cluster_method.value if document.cluster_method else None,
                is_cluster_origin=bool(cluster and cluster.origin_document_id == document.id),
                cluster_size=cluster.member_count if cluster else 1,
            )
        )
        if len(out) >= limit:
            break
    return out


def list_trending(
    session: Session, limit: int = 25, include_headwinds: bool = True
) -> list[TrendingCompanyOut]:
    """Companies ranked by how strongly they are caught up in something that is changing.

    The rating is recomputed on read rather than served from the last stored ``Score``. That
    is a deliberate cost: a stored rating outlives the evidence it was computed from, and a
    trending list that silently reflects a pipeline run from last week is exactly the kind of
    stale number this system exists not to produce. ``persist=False`` keeps a GET from
    writing.

    Headwind rows are included by default and labelled, never dropped: a competitor of a
    beneficiary is genuinely exposed to the theme, and hiding it would leave the reader to
    assume every name on the list benefits.
    """
    ratings = rate_companies(session, as_of=datetime.now(tz=UTC), persist=False)
    if not include_headwinds:
        ratings = [r for r in ratings if r.direction == "tailwind"]

    rows: list[TrendingCompanyOut] = []
    for position, rating in enumerate(ratings[:limit], start=1):
        result = rating.result
        rows.append(
            TrendingCompanyOut(
                rank=position,
                company_key=rating.company_key,
                company_name=rating.company_name,
                ticker=rating.ticker,
                rating=rating.rating,
                score=rating.score,
                direction=rating.direction,
                theme_slug=rating.theme_slug,
                theme_name=rating.theme_name,
                role=rating.role.value,
                order_of_effect=rating.order_of_effect,
                exposure_score=rating.exposure_score,
                independent_clusters=rating.independent_clusters,
                theme_count=rating.theme_count,
                data_mode=rating.data_mode.value,
                rationale=rating.rationale,
                weight_coverage=result.weight_coverage if result else 0.0,
                unavailable_components=list(result.unavailable_components) if result else [],
                components=[
                    ScoreComponentOut(
                        key=c.key,
                        label=c.label,
                        available=c.available,
                        raw_input=c.raw,
                        normalized=c.normalized,
                        weight=c.weight,
                        effective_weight=c.effective_weight,
                        contribution=c.contribution,
                        explanation=c.explanation,
                    )
                    for c in (result.components if result else [])
                ],
            )
        )
    return rows


def list_subjects(
    session: Session, limit: int = 40, discovered_only: bool = False
) -> list[SubjectOut]:
    """What the corpus turned out to be about, most salient first.

    This is the honest answer to "where did these themes come from?" — the subjects were
    mined from the documents, so a reader can check that the system is tracking something
    real rather than a vocabulary somebody typed in.
    """
    query = select(Subject).order_by(Subject.salience.desc()).limit(limit)
    if discovered_only:
        query = query.where(Subject.is_discovered.is_(True))
    return [
        SubjectOut(
            key=row.key,
            term=row.term,
            label=row.label,
            document_count=row.document_count,
            cluster_count=row.cluster_count,
            emergence=row.emergence,
            specificity=row.specificity,
            salience=row.salience,
            is_discovered=row.is_discovered,
            discovery_version=row.discovery_version,
            data_mode=row.data_mode.value,
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
        )
        for row in session.scalars(query).all()
    ]


def latest_research_run(session: Session, theme: Theme) -> ResearchRun | None:
    return session.scalar(
        select(ResearchRun)
        .where(ResearchRun.theme_id == theme.id)
        .order_by(ResearchRun.started_at.desc())
    )


def theme_report(session: Session, slug: str) -> ReportOut:
    theme = get_theme(session, slug)
    report = session.scalar(
        select(ResearchReport)
        .where(ResearchReport.theme_id == theme.id)
        .order_by(ResearchReport.generated_at.desc())
    )
    if report is None:
        raise NotFoundError(
            f"No research report has been produced for '{slug}' yet", slug=slug
        )
    return ReportOut(
        id=report.id,
        title=report.title,
        generated_at=report.generated_at,
        generator=report.generator,
        generator_version=report.generator_version,
        data_mode=report.data_mode.value,
        sections=report.sections,
    )


def theme_trace(session: Session, slug: str) -> ResearchTraceOut:
    """The durable record of what actually ran. If nothing ran, this raises."""
    theme = get_theme(session, slug)
    run = latest_research_run(session, theme)
    if run is None:
        raise NotFoundError(f"No research run exists for '{slug}'", slug=slug)

    plan = session.scalar(select(ResearchPlan).where(ResearchPlan.research_run_id == run.id))
    questions = (
        session.scalars(
            select(ResearchQuestion)
            .where(ResearchQuestion.plan_id == plan.id)
            .order_by(ResearchQuestion.priority)
        ).all()
        if plan
        else []
    )
    agent_runs = session.scalars(
        select(AgentRun).where(AgentRun.research_run_id == run.id).order_by(AgentRun.started_at)
    ).all()
    search_runs = session.scalars(
        select(SearchRun).where(SearchRun.research_run_id == run.id).order_by(SearchRun.executed_at)
    ).all()

    return ResearchTraceOut(
        research_run_id=run.id,
        status=run.status.value,
        started_at=run.started_at,
        finished_at=run.finished_at,
        stop_reason=run.stop_reason,
        plan_strategy=plan.strategy if plan else None,
        plan_rationale=plan.rationale if plan else None,
        questions=[
            QuestionOut(
                dimension=q.dimension.value,
                question=q.question,
                priority=q.priority,
                seeks_counter_evidence=q.seeks_counter_evidence,
                search_terms=(q.search_terms or {}).get("terms", []),
            )
            for q in questions
        ],
        agent_runs=[
            AgentRunOut(
                agent_name=a.agent_name,
                agent_version=a.agent_version,
                status=a.status.value,
                strategy=a.strategy,
                model=a.model,
                prompt_version=a.prompt_version,
                started_at=a.started_at,
                duration_ms=a.duration_ms,
                consumed=a.consumed,
                error=a.error,
            )
            for a in agent_runs
        ],
        search_runs=[
            SearchRunOut(
                provider_key=s.provider_key,
                provider_mode=s.provider_mode.value,
                query=s.query,
                executed_at=s.executed_at,
                result_count=s.result_count,
                new_document_count=s.new_document_count,
                latency_ms=s.latency_ms,
                status=s.status.value,
                error=s.error,
            )
            for s in search_runs
        ],
    )
