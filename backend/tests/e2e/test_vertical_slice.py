"""End-to-end vertical slice.

    source -> event -> signal -> trend -> theme -> company -> research -> report

Asserted on database state, against a real PostgreSQL database, with the same code the CLI
and the API use. This is the test that says whether the architecture actually works.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from marketradar.demo.loader import seed_demo_universe
from marketradar.domain.enums import (
    ClaimType,
    DataMode,
    EntityType,
    FindingStance,
    RunStatus,
    TrendMaturity,
)
from marketradar.domain.models import (
    AgentRun,
    Company,
    EntityRelationship,
    Event,
    EvidenceCluster,
    EvidenceItem,
    ResearchFinding,
    ResearchPlan,
    ResearchQuestion,
    ResearchReport,
    ResearchRun,
    Score,
    ScoreComponent,
    SearchRun,
    Signal,
    SignalObservation,
    Source,
    SourceDocument,
    Theme,
    ThemeCompanyExposure,
    Trend,
)
from marketradar.entities.relationships import RELATIONSHIP_EXTRACTOR_VERSION
from marketradar.evidence.extractor import EXTRACTOR_VERSION
from marketradar.orchestration import run_pipeline
from marketradar.providers import build_default_registry
from marketradar.research.loop import run_research

pytestmark = [pytest.mark.integration, pytest.mark.e2e]

#: Fixed instant so the windows land deterministically over the corpus.
AS_OF = datetime(2026, 9, 8, tzinfo=UTC)
THEME_SLUG = "ai-memory-demand"


@pytest.fixture
def pipeline(session, settings):
    seed_demo_universe(session)
    registry = build_default_registry(settings)
    result = run_pipeline(session, as_of=AS_OF, settings=settings, registry=registry)
    return result, registry


# ------------------------------------------------------- source -> event
def test_documents_are_ingested_with_full_provenance(session, pipeline):
    result, _ = pipeline
    assert result.ingestion.documents_created > 20

    document = session.scalars(select(SourceDocument)).first()
    assert document.content_hash and len(document.content_hash) == 64
    assert document.published_at and document.event_at and document.retrieved_at
    assert document.provider_key
    assert document.data_mode == DataMode.DEMO


def test_repeated_reporting_collapses_into_one_confirmation(session, pipeline):
    """The corpus contains one announcement carried by five outlets."""
    clusters = session.scalars(select(EvidenceCluster)).all()
    documents = session.scalar(select(func.count()).select_from(SourceDocument))
    assert len(clusters) < documents

    biggest = max(clusters, key=lambda c: c.member_count)
    assert biggest.member_count == 5
    assert biggest.origin_document_id is not None
    origin = session.get(SourceDocument, biggest.origin_document_id)
    # The primary announcement, not the first outlet to rewrite it, is the origin.
    origin_source = session.get(Source, origin.source_id)
    assert origin_source.base_quality >= 90


def test_amplified_evidence_produces_a_single_event(session, pipeline):
    """Five documents about one announcement must not become five events."""
    clusters = session.scalars(select(EvidenceCluster)).all()
    biggest = max(clusters, key=lambda c: c.member_count)
    evidence = session.scalars(
        select(EvidenceItem).where(EvidenceItem.cluster_id == biggest.id)
    ).all()
    events = session.scalars(select(Event).where(Event.cluster_id == biggest.id)).all()
    assert len(evidence) > len(events)
    for event in events:
        assert event.cluster_id == biggest.id


def test_forward_looking_statements_are_evidence_but_never_events(session, pipeline):
    speculative = session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.event_type.is_(None),
            # Relationship disclosures also carry no event type — they are standing facts,
            # not observed changes — but they are a different kind of claim and have no
            # magnitude at all. This test is about projections and historical analogies.
            EvidenceItem.extractor_version == EXTRACTOR_VERSION,
        )
    ).all()
    assert speculative, "the corpus contains risk arguments and historical analogies"
    for item in speculative:
        assert item.magnitude == 0.0


def test_relationship_disclosures_are_evidence_but_never_events(session, pipeline):
    """The other class of event-less evidence: who supplies whom (audit C5)."""
    disclosures = session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.extractor_version == RELATIONSHIP_EXTRACTOR_VERSION
        )
    ).all()
    assert disclosures, "the corpus contains filings that name suppliers and customers"
    for item in disclosures:
        assert item.event_type is None
        # A standing relationship has no magnitude; recording 0.0 would imply a measured
        # change of size zero, which is a different statement.
        assert item.magnitude is None


# ------------------------------------------------------- event -> signal
def test_signals_are_computed_with_stored_inputs(session, pipeline):
    signals = session.scalars(select(Signal)).all()
    assert len(signals) >= 5

    observations = session.scalars(
        select(SignalObservation).where(SignalObservation.strength > 0)
    ).all()
    assert observations
    for observation in observations:
        assert 0 <= observation.strength <= 100
        assert observation.inputs and "contributions" in observation.inputs
        assert observation.computation_version


# ------------------------------------------------------- signal -> trend
def test_trends_compare_the_window_against_its_own_baseline(session, pipeline):
    trends = session.scalars(select(Trend)).all()
    assert trends
    for trend in trends:
        assert trend.baseline_start < trend.baseline_end <= trend.observation_start
        assert -100 <= trend.acceleration <= 100
        assert trend.inputs and "baseline_bucket_mean" in trend.inputs


def test_the_memory_signals_accelerate_against_their_baseline(session, pipeline):
    demand = session.scalar(select(Signal).where(Signal.key == "memory_demand"))
    trend = session.scalar(select(Trend).where(Trend.signal_id == demand.id))
    assert trend.acceleration > 40
    assert trend.observation_strength > trend.baseline_strength


# -------------------------------------------------------- trend -> theme
def test_the_theme_forms_from_measured_signals(session, pipeline):
    result, _ = pipeline
    assert THEME_SLUG in result.themes

    theme = session.scalar(select(Theme).where(Theme.slug == THEME_SLUG))
    assert theme.maturity == TrendMaturity.ACCELERATING
    assert theme.data_mode == DataMode.DEMO
    assert set(theme.anchor_entities["subjects"]) == {"memory", "ai_infrastructure"}


def test_scores_are_stored_with_their_full_decomposition(session, pipeline):
    theme = session.scalar(select(Theme).where(Theme.slug == THEME_SLUG))
    scores = session.scalars(
        select(Score).where(Score.subject_type == "THEME", Score.subject_id == theme.id)
    ).all()
    names = {s.model_name for s in scores}
    assert names == {"trend_score", "confidence_score", "opportunity_score"}

    for score in scores:
        components = session.scalars(
            select(ScoreComponent).where(ScoreComponent.score_id == score.id)
        ).all()
        assert components
        assert all(c.explanation for c in components)
        assert 0 <= score.value <= 100


def test_unavailable_inputs_are_excluded_and_declared_not_defaulted(session, pipeline):
    """No market-data provider is configured; the score must say so, not guess."""
    theme = session.scalar(select(Theme).where(Theme.slug == THEME_SLUG))
    trend_score = session.scalar(
        select(Score).where(
            Score.subject_id == theme.id, Score.model_name == "trend_score"
        )
    )
    dropped = trend_score.unavailable_components["keys"]
    assert "market_mispricing" in dropped
    assert trend_score.weight_coverage < 1.0

    component = session.scalar(
        select(ScoreComponent).where(
            ScoreComponent.score_id == trend_score.id,
            ScoreComponent.key == "market_mispricing",
        )
    )
    assert component.available is False
    assert component.normalized is None, "an unavailable input is never a number"
    assert component.effective_weight == 0.0
    assert "market-data" in component.explanation


def test_confidence_and_opportunity_are_reported_separately(session, pipeline):
    theme = session.scalar(select(Theme).where(Theme.slug == THEME_SLUG))
    scores = {
        s.model_name: s
        for s in session.scalars(select(Score).where(Score.subject_id == theme.id)).all()
    }
    assert scores["confidence_score"].value != scores["opportunity_score"].value


# ------------------------------------------------------ theme -> company
def test_the_value_chain_is_read_from_documents_not_typed_in(session, pipeline):
    """Audit C5: every graph edge was hand-entered and not one carried evidence.

    The bar is not "some edges have citations" but that the company-to-company edges the
    traversal actually crosses are, in the main, traceable to a sentence in a document.
    A hand-entered edge is a claim no reader can check.
    """
    edges = session.scalars(select(EntityRelationship)).all()
    company_edges = [
        edge
        for edge in edges
        if edge.source_entity_type is EntityType.COMPANY
        and edge.target_entity_type is EntityType.COMPANY
    ]
    assert company_edges
    cited = [edge for edge in company_edges if edge.evidence_id is not None]
    # A ratchet, not a ceiling. Raise it as coverage improves; never lower it to pass.
    assert len(cited) / len(company_edges) >= 0.75, (
        f"only {len(cited)}/{len(company_edges)} company edges carry evidence"
    )

    for edge in cited:
        evidence = session.get(EvidenceItem, edge.evidence_id)
        assert evidence is not None
        document = session.get(SourceDocument, evidence.document_id)
        assert document is not None
        # The citation must resolve to the exact span it claims, in the real document.
        assert (
            document.body_text[evidence.excerpt_start : evidence.excerpt_end].strip()
            == evidence.excerpt
        )
        # An edge is never more confident than the sentence holding it up.
        assert edge.confidence <= evidence.confidence + 1e-9


def test_the_value_chain_reaches_first_second_and_third_order_companies(session, pipeline):
    theme = session.scalar(select(Theme).where(Theme.slug == THEME_SLUG))
    exposures = session.scalars(
        select(ThemeCompanyExposure).where(ThemeCompanyExposure.theme_id == theme.id)
    ).all()
    assert len(exposures) >= 6
    orders = {e.order_of_effect for e in exposures}
    assert {1, 2, 3} <= orders, "second- and third-order discovery is the differentiator"

    for exposure in exposures:
        assert exposure.path and exposure.path["hops"], "the path IS the explanation"
        assert exposure.rationale
        assert 0 <= exposure.exposure_score <= 100


def test_every_mapped_company_is_fictional_in_demo_mode(session, pipeline):
    companies = session.scalars(select(Company)).all()
    assert companies
    assert all(c.is_fictional and c.data_mode == DataMode.DEMO for c in companies)


# ---------------------------------------------------- theme -> research
@pytest.fixture
def research(session, settings, pipeline):
    _, registry = pipeline
    return run_research(
        session, theme_slug=THEME_SLUG, as_of=AS_OF, settings=settings, registry=registry
    )


def test_the_research_run_completes_and_records_why_it_stopped(session, research):
    assert research.status == RunStatus.SUCCEEDED
    assert research.stop_reason in {"plan_complete", "saturation", "search_budget_exhausted"}

    run = session.get(ResearchRun, research.research_run_id)
    assert run.finished_at is not None
    assert run.consumed is not None


def test_the_plan_covers_counter_evidence_and_records_its_strategy(session, research):
    run = session.get(ResearchRun, research.research_run_id)
    plan = session.scalar(select(ResearchPlan).where(ResearchPlan.research_run_id == run.id))
    assert plan.strategy in {"rule_based", "llm"}
    assert plan.rationale

    questions = session.scalars(
        select(ResearchQuestion).where(ResearchQuestion.plan_id == plan.id)
    ).all()
    assert len(questions) >= 10
    assert any(q.seeks_counter_evidence for q in questions)


def test_the_agent_run_is_traced(session, research):
    """If no agent ran, there would be no trace — the UI cannot show a fabricated one."""
    runs = session.scalars(
        select(AgentRun).where(AgentRun.research_run_id == research.research_run_id)
    ).all()
    assert len(runs) == 1
    trace = runs[0]
    assert trace.agent_name == "research_planner"
    assert trace.status == RunStatus.SUCCEEDED
    assert trace.input_payload and trace.output_payload
    assert trace.duration_ms is not None
    # No LLM configured in this environment: the trace must say so rather than imply one.
    assert trace.strategy == "rule_based"
    assert trace.model is None


def test_searches_are_recorded_with_their_provider_and_mode(session, research):
    searches = session.scalars(
        select(SearchRun).where(SearchRun.research_run_id == research.research_run_id)
    ).all()
    assert searches
    for search in searches:
        assert search.query
        assert search.provider_mode in set(DataMode)
        assert search.status in set(RunStatus)


def test_findings_carry_evidence_stance_and_claim_type(session, research):
    findings = session.scalars(
        select(ResearchFinding).where(
            ResearchFinding.research_run_id == research.research_run_id
        )
    ).all()
    assert len(findings) >= 8
    assert all(f.claim_type in set(ClaimType) for f in findings)
    assert all(0.0 <= f.confidence <= 1.0 for f in findings)

    stances = {f.stance for f in findings}
    assert FindingStance.SUPPORTING in stances
    assert FindingStance.CONTRADICTING in stances, "counter-evidence must survive to the report"


def test_a_dimension_with_no_evidence_says_so(session, research):
    """A silent gap in the research is indistinguishable from a gap in the world."""
    findings = session.scalars(
        select(ResearchFinding).where(
            ResearchFinding.research_run_id == research.research_run_id,
            ResearchFinding.independent_source_count == 0,
        )
    ).all()
    assert any("No evidence" in f.claim for f in findings)


# ---------------------------------------------------- research -> report
def test_the_report_answers_the_four_questions(session, research):
    report = session.get(ResearchReport, research.report_id)
    sections = report.sections

    for required in (
        "what_changed",
        "why_now",
        "why_it_matters",
        "supporting_evidence",
        "contradictory_evidence",
        "companies_exposed",
        "market_awareness",
        "invalidation_conditions",
        "scores",
        "provenance",
    ):
        assert required in sections, required

    assert sections["what_changed"], "what changed"
    assert sections["why_now"], "why now"
    assert sections["market_awareness"], "what the market knows"
    assert sections["contradictory_evidence"], "what says we are wrong"


def test_every_report_claim_declares_its_epistemic_status(session, research):
    report = session.get(ResearchReport, research.report_id)
    for key in ("what_changed", "why_now", "supporting_evidence", "contradictory_evidence"):
        for line in report.sections[key]:
            assert line["claim_type"] in {c.value for c in ClaimType}
            assert line["text"]


def test_report_evidence_ids_resolve_to_real_evidence(session, research):
    """'Where did this come from?' must resolve for every cited line."""
    report = session.get(ResearchReport, research.report_id)
    cited: set[str] = set()
    for key in ("supporting_evidence", "contradictory_evidence"):
        for line in report.sections[key]:
            cited.update(line["evidence_ids"])
    assert cited, "the report cites evidence"

    for evidence_id in cited:
        item = session.get(EvidenceItem, evidence_id)
        assert item is not None
        document = session.get(SourceDocument, item.document_id)
        assert document is not None and document.url
        assert session.get(Source, document.source_id) is not None


def test_the_report_declares_its_data_mode_and_missing_providers(session, research):
    report = session.get(ResearchReport, research.report_id)
    provenance = report.sections["provenance"]
    assert provenance["data_mode"] == DataMode.DEMO.value
    assert provenance["provider_modes"]["MARKET_DATA"] == DataMode.UNAVAILABLE.value
    assert "not investment advice" in provenance["disclaimer"].lower()
    assert provenance["generator"] == "deterministic_assembler"


def test_invalidation_conditions_are_measurable_rules(session, research):
    report = session.get(ResearchReport, research.report_id)
    conditions = report.sections["invalidation_conditions"]
    assert len(conditions) >= 2
    for condition in conditions:
        assert condition["signal"]
        assert condition["threshold"] is not None
        assert condition["condition"]


# ------------------------------------------------------------ idempotency
def test_rerunning_the_pipeline_changes_nothing(session, settings, pipeline):
    _, registry = pipeline

    def counts():
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                SourceDocument, EvidenceItem, Event, EvidenceCluster,
                Theme, ThemeCompanyExposure, Score,
            )
        }

    before = counts()
    second = run_pipeline(session, as_of=AS_OF, settings=settings, registry=registry)
    assert counts() == before
    assert second.ingestion.documents_created == 0
    assert second.ingestion.documents_skipped > 0
