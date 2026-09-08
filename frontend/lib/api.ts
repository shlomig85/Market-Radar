/**
 * Typed client for the Market Radar API.
 *
 * Server components call these directly. Every response type mirrors the backend schema:
 * if a field is absent there, it is absent here, and the UI renders "unavailable" rather
 * than inventing a placeholder.
 */

const BASE = process.env.MARKETRADAR_API_URL ?? "http://localhost:8000";

export type DataMode = "LIVE" | "HISTORICAL" | "DEMO" | "UNAVAILABLE";

export interface ProviderHealth {
  capability: string;
  provider_key: string;
  available: boolean;
  mode: DataMode;
  detail: string;
  live_path_verified: boolean;
  checked_at: string;
}

export interface ScoreComponent {
  key: string;
  label: string;
  available: boolean;
  raw_input: number | null;
  normalized: number | null;
  weight: number;
  effective_weight: number;
  contribution: number;
  explanation: string;
}

export interface Score {
  model_name: string;
  model_version: string;
  value: number;
  weight_coverage: number;
  unavailable_components: string[];
  computed_at: string;
  data_mode: DataMode;
  notes: string | null;
  components: ScoreComponent[];
}

export interface Trend {
  signal_key: string;
  signal_name: string;
  category: string;
  observation_strength: number;
  baseline_strength: number;
  acceleration: number;
  frequency_change: number;
  independence_change: number;
  direction: "POSITIVE" | "NEGATIVE" | "NEUTRAL";
  observation_start: string;
  observation_end: string;
  baseline_start: string;
  baseline_end: string;
  data_mode: DataMode;
}

export interface Hop {
  from: string;
  relationship: string;
  direction: string;
  to: string;
  edge_weight: number;
  edge_confidence: number;
}

export interface Exposure {
  company_key: string;
  company_name: string;
  ticker: string | null;
  is_fictional: boolean;
  role: string;
  order_of_effect: number;
  exposure_score: number;
  confidence: number;
  path: string | null;
  hops: Hop[];
  data_mode: DataMode;
}

export interface ThemeSummary {
  slug: string;
  name: string;
  summary: string | null;
  maturity: string;
  maturity_stage: number;
  market_awareness: string;
  market_awareness_mode: DataMode;
  data_mode: DataMode;
  first_detected_at: string;
  last_updated_at: string;
  trend_score: number | null;
  confidence_score: number | null;
  opportunity_score: number | null;
  top_acceleration: number | null;
  company_count: number;
  independent_source_count: number;
}

export interface ThemeDetail {
  theme: ThemeSummary;
  scores: Score[];
  trends: Trend[];
  exposures: Exposure[];
  evidence_count: number;
  independent_source_count: number;
  amplification_ratio: number;
  contradiction_ratio: number;
}

export interface Evidence {
  id: string;
  claim: string;
  excerpt: string;
  confidence: number;
  event_type: string | null;
  direction: string;
  subject_key: string | null;
  rule_key: string;
  extracted_by: string;
  extractor_version: string;
  data_mode: DataMode;
  event_at: string;
  published_at: string;
  retrieved_at: string;
  event_at_inferred: boolean;
  document_id: string;
  document_title: string;
  document_url: string;
  source_key: string;
  source_name: string;
  publisher: string;
  source_type: string;
  source_class: string;
  source_quality: number;
  is_synthetic_source: boolean;
  cluster_id: string | null;
  cluster_method: string | null;
  is_cluster_origin: boolean;
  cluster_size: number;
}

export interface ReportLine {
  text: string;
  claim_type: string;
  evidence_ids: string[];
  dimension?: string;
  confidence?: number;
  independent_sources?: number;
  reasoning?: string;
}

export interface Report {
  id: string;
  title: string;
  generated_at: string;
  generator: string;
  generator_version: string;
  data_mode: DataMode;
  sections: {
    what_changed: ReportLine[];
    why_now: ReportLine[];
    why_it_matters: ReportLine[];
    supporting_evidence: ReportLine[];
    contradictory_evidence: ReportLine[];
    contextual_findings: ReportLine[];
    research_gaps: ReportLine[];
    market_awareness: ReportLine[];
    companies_exposed: unknown[];
    invalidation_conditions: {
      signal: string;
      signal_name: string;
      condition: string;
      current_value: number | null;
      threshold: number;
    }[];
    provenance: {
      generator: string;
      generator_version: string;
      pipeline_version: string;
      data_mode: DataMode;
      as_of: string;
      provider_modes: Record<string, DataMode>;
      disclaimer: string;
    };
  };
}

export interface Trace {
  research_run_id: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  stop_reason: string | null;
  plan_strategy: string | null;
  plan_rationale: string | null;
  questions: {
    dimension: string;
    question: string;
    priority: number;
    seeks_counter_evidence: boolean;
    search_terms: string[];
  }[];
  agent_runs: {
    agent_name: string;
    agent_version: string;
    status: string;
    strategy: string;
    model: string | null;
    prompt_version: string | null;
    started_at: string;
    duration_ms: number | null;
    consumed: Record<string, unknown> | null;
    error: string | null;
  }[];
  search_runs: {
    provider_key: string;
    provider_mode: DataMode;
    query: string;
    executed_at: string;
    result_count: number;
    new_document_count: number;
    latency_ms: number | null;
    status: string;
    error: string | null;
  }[];
}

/**
 * Fetch helper.
 *
 * Returns `null` on 404 and on connection failure so pages can render an honest empty
 * state instead of a crash — a backend that is not running is a state the user should see
 * described, not a stack trace.
 */
async function get<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${BASE}${path}`, { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export const api = {
  providers: () => get<ProviderHealth[]>("/providers"),
  themes: () => get<ThemeSummary[]>("/themes"),
  theme: (slug: string) => get<ThemeDetail>(`/themes/${slug}`),
  evidence: (slug: string) => get<Evidence[]>(`/themes/${slug}/evidence`),
  report: (slug: string) => get<Report>(`/themes/${slug}/report`),
  trace: (slug: string) => get<Trace>(`/themes/${slug}/trace`),
};
