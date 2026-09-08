"""Pipeline orchestration.

Runs the deterministic half of the system end to end:

    ingest -> cluster -> extract -> events -> signals -> trends -> themes
           -> exposure -> scores

Each stage is idempotent, so the whole pipeline is safe to re-run. The orchestrator returns
a structured result rather than printing, so the CLI, the tests and (later) a worker can all
drive it the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from marketradar.bus import DomainEvent, get_bus
from marketradar.config import Settings, get_settings
from marketradar.domain.enums import DataMode, EntityType, ProviderCapability
from marketradar.domain.models import Signal, Theme, Trend
from marketradar.ingestion.pipeline import (
    IngestionReport,
    build_events,
    extract_evidence,
    ingest_documents,
    rebuild_clusters,
    upsert_sources,
)
from marketradar.logging import get_logger
from marketradar.mapping.exposure import compute_exposures
from marketradar.mapping.value_chain import Anchor
from marketradar.providers import ProviderRegistry, build_default_registry
from marketradar.scoring import persist_score
from marketradar.signals.definitions import SIGNAL_DEFINITIONS, SIGNALS_BY_KEY
from marketradar.signals.engine import SignalEngine
from marketradar.themes.definitions import SUBJECT_ANCHORS
from marketradar.themes.formation import FormedTheme, form_themes
from marketradar.themes.scoring import collect_metrics, confidence_score, opportunity_score
from marketradar.themes.scoring import trend_score as compute_trend_score
from marketradar.trends.engine import TrendEngine
from marketradar.trends.maturity import MaturityInputs, classify

log = get_logger(__name__)


@dataclass
class PipelineResult:
    ingestion: IngestionReport = field(default_factory=IngestionReport)
    signals_computed: int = 0
    trends_computed: int = 0
    themes: list[str] = field(default_factory=list)
    exposures_created: int = 0
    scores_created: int = 0
    provider_modes: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def run_pipeline(
    session: Session,
    as_of: datetime | None = None,
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
) -> PipelineResult:
    """Execute the full deterministic pipeline."""
    settings = settings or get_settings()
    registry = registry or build_default_registry(settings)
    as_of = as_of or datetime.now(tz=UTC)
    result = PipelineResult()

    for health in registry.health_report():
        result.provider_modes[health.capability.value] = health.mode.value
        if health.mode == DataMode.UNAVAILABLE:
            result.notes.append(f"{health.capability.value}: {health.detail}")

    # --- 1. ingest -----------------------------------------------------
    for capability in (ProviderCapability.NEWS_SEARCH, ProviderCapability.FILINGS):
        provider = registry.get(capability)
        health = provider.health()
        if not health.available:
            continue
        sources = upsert_sources(session, provider.sources())
        fetch = getattr(provider, "get_recent_documents", None)
        payload = fetch(limit=500) if fetch else provider.search(_catchall_query())
        result.ingestion = result.ingestion.merge(
            ingest_documents(session, payload, sources, now=as_of)
        )

    # --- 2. ancestry, evidence, events ---------------------------------
    result.ingestion = result.ingestion.merge(rebuild_clusters(session, settings))
    result.ingestion = result.ingestion.merge(extract_evidence(session))
    # Clusters are rebuilt again so evidence created in this run inherits its document's
    # cluster; the operation is idempotent, so the second pass is cheap and keeps the
    # denormalised link exact.
    rebuild_clusters(session, settings)
    result.ingestion = result.ingestion.merge(build_events(session))

    # --- 3. signals ----------------------------------------------------
    engine = SignalEngine(session)
    for definition in SIGNAL_DEFINITIONS:
        engine.compute_signal(definition, as_of=as_of)
        result.signals_computed += 1
    get_bus().publish(DomainEvent.SIGNAL_UPDATED, {"count": result.signals_computed})

    # --- 4. trends -----------------------------------------------------
    trend_engine = TrendEngine(
        session,
        engine,
        observation_window_days=settings.observation_window_days,
        baseline_window_days=settings.baseline_window_days,
    )
    trends: dict[str, Trend] = {}
    for signal in session.scalars(select(Signal)).all():
        signal_definition = SIGNALS_BY_KEY.get(signal.key)
        if signal_definition is None:
            continue
        computed = trend_engine.compute(signal, signal_definition, as_of)
        trends[signal.key] = trend_engine.persist(signal, computed)
        result.trends_computed += 1
    get_bus().publish(DomainEvent.TREND_UPDATED, {"count": result.trends_computed})

    # --- 5. themes -----------------------------------------------------
    formed = form_themes(session, trends, as_of)
    for theme_result in formed:
        _finalise_theme(session, theme_result, as_of, result)
        result.themes.append(theme_result.theme.slug)
    get_bus().publish(DomainEvent.THEME_UPDATED, {"themes": result.themes})

    log.info(
        "pipeline.complete",
        documents_created=result.ingestion.documents_created,
        events_created=result.ingestion.events_created,
        themes=result.themes,
    )
    return result


def _finalise_theme(
    session: Session, formed: FormedTheme, as_of: datetime, result: PipelineResult
) -> None:
    """Map companies, classify maturity and compute the theme's three scores."""
    theme = formed.theme
    subjects = (theme.anchor_entities or {}).get("subjects", [])
    anchors = [
        Anchor(entity_type=EntityType(spec[0]), entity_key=spec[1], weight=spec[2])
        for subject in subjects
        if (spec := SUBJECT_ANCHORS.get(subject)) is not None
    ]
    exposures = compute_exposures(session, theme, anchors, as_of)
    result.exposures_created += len(exposures)

    metrics = collect_metrics(session, theme, formed.activations, as_of)

    maturity = classify(
        MaturityInputs(
            independent_source_count=metrics.independence.independent_source_count,
            acceleration=metrics.acceleration,
            observation_strength=metrics.strength,
            mainstream_coverage_share=metrics.mainstream_share,
            market_reaction_observed=metrics.market_reaction_observed,
            contradiction_ratio=metrics.contradiction_ratio,
        )
    )
    theme.maturity = maturity.maturity
    theme.market_awareness = maturity.market_awareness
    # The awareness estimate is coverage-derived, not measured from market data. Recording
    # its mode separately is what lets the UI avoid implying a measurement we did not make.
    theme.market_awareness_mode = (
        DataMode.DEMO if metrics.data_mode == DataMode.DEMO else DataMode.UNAVAILABLE
    )

    confidence = confidence_score(metrics)
    trend = compute_trend_score(metrics, confidence.value)
    opportunity = opportunity_score(metrics, trend)

    for score in (trend, confidence, opportunity):
        persist_score(
            session,
            score,
            subject_type="THEME",
            subject_id=theme.id,
            data_mode=metrics.data_mode,
            computed_at=as_of,
            notes=maturity.rationale if score is trend else None,
        )
        result.scores_created += 1
    session.flush()


def _catchall_query():  # type: ignore[no-untyped-def]
    from marketradar.providers.base import ProviderQuery

    return ProviderQuery(text="", terms=(), limit=500)


def latest_theme(session: Session, slug: str) -> Theme | None:
    return session.scalar(select(Theme).where(Theme.slug == slug))
