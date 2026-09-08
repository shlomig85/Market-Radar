"""API response schemas.

Deliberately explicit rather than ORM passthrough: the wire format is a contract with the
frontend and should not shift because a column was renamed.

Two conventions run through all of them:

* anything derived from outside data carries its ``data_mode``, so the UI can label it;
* any score carries its component decomposition and the list of components that could not
  be computed, so a number is never presented without its basis.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProviderHealthOut(BaseModel):
    capability: str
    provider_key: str
    available: bool
    mode: str
    detail: str
    live_path_verified: bool
    checked_at: datetime


class ScoreComponentOut(BaseModel):
    key: str
    label: str
    available: bool
    raw_input: float | None
    normalized: float | None
    weight: float
    effective_weight: float
    contribution: float
    explanation: str


class ScoreOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    model_version: str
    value: float
    weight_coverage: float
    unavailable_components: list[str]
    computed_at: datetime
    data_mode: str
    notes: str | None
    components: list[ScoreComponentOut]


class TrendOut(BaseModel):
    signal_key: str
    signal_name: str
    category: str
    observation_strength: float
    baseline_strength: float
    acceleration: float
    frequency_change: float
    independence_change: float
    direction: str
    observation_start: datetime
    observation_end: datetime
    baseline_start: datetime
    baseline_end: datetime
    data_mode: str


class ExposureOut(BaseModel):
    company_key: str
    company_name: str
    ticker: str | None
    is_fictional: bool
    role: str
    order_of_effect: int
    exposure_score: float
    confidence: float
    path: str | None
    hops: list[dict]
    data_mode: str


class ThemeSummaryOut(BaseModel):
    slug: str
    name: str
    summary: str | None
    maturity: str
    maturity_stage: int
    market_awareness: str
    market_awareness_mode: str
    data_mode: str
    first_detected_at: datetime
    last_updated_at: datetime
    trend_score: float | None
    confidence_score: float | None
    opportunity_score: float | None
    top_acceleration: float | None
    company_count: int
    independent_source_count: int


class EvidenceOut(BaseModel):
    id: str
    claim: str
    excerpt: str
    confidence: float
    event_type: str | None
    direction: str
    subject_key: str | None
    rule_key: str
    extracted_by: str
    extractor_version: str
    data_mode: str
    event_at: datetime
    published_at: datetime
    retrieved_at: datetime
    event_at_inferred: bool
    # provenance chain
    document_id: str
    document_title: str
    document_url: str
    source_key: str
    source_name: str
    publisher: str
    source_type: str
    source_class: str
    source_quality: int
    is_synthetic_source: bool
    cluster_id: str | None
    cluster_method: str | None
    is_cluster_origin: bool
    cluster_size: int


class ThemeDetailOut(BaseModel):
    theme: ThemeSummaryOut
    scores: list[ScoreOut]
    trends: list[TrendOut]
    exposures: list[ExposureOut]
    evidence_count: int
    independent_source_count: int
    amplification_ratio: float
    contradiction_ratio: float


class SearchRunOut(BaseModel):
    provider_key: str
    provider_mode: str
    query: str
    executed_at: datetime
    result_count: int
    new_document_count: int
    latency_ms: int | None
    status: str
    error: str | None


class AgentRunOut(BaseModel):
    agent_name: str
    agent_version: str
    status: str
    strategy: str
    model: str | None
    prompt_version: str | None
    started_at: datetime
    duration_ms: int | None
    consumed: dict | None
    error: str | None


class QuestionOut(BaseModel):
    dimension: str
    question: str
    priority: int
    seeks_counter_evidence: bool
    search_terms: list[str]


class ResearchTraceOut(BaseModel):
    research_run_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    stop_reason: str | None
    plan_strategy: str | None
    plan_rationale: str | None
    questions: list[QuestionOut]
    agent_runs: list[AgentRunOut]
    search_runs: list[SearchRunOut]


class ReportOut(BaseModel):
    id: str
    title: str
    generated_at: datetime
    generator: str
    generator_version: str
    data_mode: str
    sections: dict
