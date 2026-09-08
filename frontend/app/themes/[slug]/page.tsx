import Link from "next/link";
import { notFound } from "next/navigation";

import { api, type Evidence, type ReportLine, type Score } from "@/lib/api";
import {
  ClaimTag,
  DataModeBadge,
  Delta,
  Empty,
  MaturityRail,
  Metric,
  NotImplemented,
  Panel,
} from "@/components/primitives";

export const dynamic = "force-dynamic";

function ScoreBreakdown({ score }: { score: Score }) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="numeric text-3xl text-ink">{score.value.toFixed(0)}</span>
        <span className="numeric text-xs text-faint">
          computed on {(score.weight_coverage * 100).toFixed(0)}% of model weight · v
          {score.model_version}
        </span>
      </div>
      <table className="mt-3 w-full text-xs">
        <tbody>
          {score.components.map((component) => (
            <tr key={component.key} className="border-b border-line/40 align-top last:border-0">
              <td className="w-44 py-1.5 pr-2">
                <span className={component.available ? "text-ink" : "text-faint"}>
                  {component.label}
                </span>
              </td>
              <td className="w-24 py-1.5 pr-2">
                {component.available ? (
                  <div>
                    <span className="numeric text-signal">
                      {component.normalized?.toFixed(0)}
                    </span>
                    <div className="mt-0.5 h-[2px] w-16 bg-line">
                      <div
                        className="h-full bg-signal"
                        style={{ width: `${component.normalized ?? 0}%` }}
                      />
                    </div>
                  </div>
                ) : (
                  <span className="numeric text-faint">excluded</span>
                )}
              </td>
              <td className="w-16 py-1.5 pr-2">
                <span className="numeric text-faint">
                  {component.available
                    ? `w ${component.effective_weight.toFixed(2)}`
                    : `w ${component.weight.toFixed(2)}→0`}
                </span>
              </td>
              <td className="py-1.5 text-muted">{component.explanation}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {score.unavailable_components.length > 0 && (
        <p className="mt-2 border-t border-line pt-2 text-[11px] text-faint">
          Excluded for want of data: {score.unavailable_components.join(", ")}. The remaining
          weights were renormalised; no missing input was replaced with a default.
        </p>
      )}
    </div>
  );
}

function EvidenceRow({ item }: { item: Evidence }) {
  return (
    <details className="border-b border-line/50 py-2 last:border-0">
      <summary className="cursor-pointer list-none">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="numeric text-[10px] text-faint">
            {item.event_at.slice(0, 10)}
          </span>
          <span className="text-sm text-ink">{item.claim}</span>
          {item.event_type ? (
            <span className="numeric text-[10px] text-signal">{item.event_type}</span>
          ) : (
            <span
              className="numeric text-[10px] text-warn"
              title="Forward-looking or historical statement: evidence, but not an observed event"
            >
              NO EVENT
            </span>
          )}
          {item.cluster_size > 1 && (
            <span
              className="numeric text-[10px] text-demo"
              title="This evidence belongs to a cluster of documents reporting one underlying announcement"
            >
              {item.is_cluster_origin
                ? `ORIGIN of ${item.cluster_size}`
                : `1 of ${item.cluster_size} · ${item.cluster_method}`}
            </span>
          )}
        </div>
      </summary>
      <div className="mt-2 space-y-2 border-l-2 border-line pl-3">
        <blockquote className="text-xs italic text-muted">&ldquo;{item.excerpt}&rdquo;</blockquote>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-[11px] sm:grid-cols-4">
          <div>
            <dt className="label">Publisher</dt>
            <dd className="text-muted">
              {item.publisher}
              {item.is_synthetic_source && (
                <span className="ml-1 text-demo">(synthetic)</span>
              )}
            </dd>
          </div>
          <div>
            <dt className="label">Source type</dt>
            <dd className="text-muted">
              {item.source_type} · quality {item.source_quality}
            </dd>
          </div>
          <div>
            <dt className="label">Published</dt>
            <dd className="numeric text-muted">{item.published_at.slice(0, 10)}</dd>
          </div>
          <div>
            <dt className="label">Event occurred</dt>
            <dd className="numeric text-muted">
              {item.event_at.slice(0, 10)}
              {item.event_at_inferred && (
                <span className="ml-1 text-warn" title="Event time inferred from publication">
                  inferred
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt className="label">Retrieved</dt>
            <dd className="numeric text-muted">{item.retrieved_at.slice(0, 10)}</dd>
          </div>
          <div>
            <dt className="label">Extractor</dt>
            <dd className="numeric text-muted">
              {item.extracted_by} v{item.extractor_version} · {item.rule_key}
            </dd>
          </div>
          <div>
            <dt className="label">Confidence</dt>
            <dd className="numeric text-muted">{(item.confidence * 100).toFixed(0)}</dd>
          </div>
          <div>
            <dt className="label">Provenance</dt>
            <dd>
              <DataModeBadge mode={item.data_mode} />
            </dd>
          </div>
        </dl>
        <p className="text-[11px] text-faint">
          Document:{" "}
          <span className="text-muted">{item.document_title}</span>{" "}
          <span className="numeric break-all">({item.document_url})</span>
          {item.document_url.includes(".invalid") && (
            <span className="ml-1 text-demo">
              — non-resolvable by design: synthetic source
            </span>
          )}
        </p>
      </div>
    </details>
  );
}

function Findings({ lines, tone }: { lines: ReportLine[]; tone: "favour" | "against" | "muted" }) {
  if (lines.length === 0) {
    return <Empty title="Nothing found." hint="No finding in this category for this run." />;
  }
  const border = {
    favour: "border-favour/40",
    against: "border-against/40",
    muted: "border-line",
  }[tone];
  return (
    <ul className="space-y-3">
      {lines.map((line, index) => (
        <li key={index} className={`border-l-2 ${border} pl-3`}>
          <div className="flex flex-wrap items-baseline gap-2">
            <ClaimTag type={line.claim_type} />
            {line.dimension && (
              <span className="numeric text-[10px] text-faint">{line.dimension}</span>
            )}
            {line.independent_sources !== undefined && (
              <span className="numeric text-[10px] text-muted">
                {line.independent_sources} independent
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-ink">{line.text}</p>
          {line.reasoning && <p className="mt-1 text-[11px] text-faint">{line.reasoning}</p>}
        </li>
      ))}
    </ul>
  );
}

export default async function ThemePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const [detail, evidence, report, trace] = await Promise.all([
    api.theme(slug),
    api.evidence(slug),
    api.report(slug),
    api.trace(slug),
  ]);

  if (!detail) notFound();
  const { theme, scores, trends, exposures } = detail;
  const byModel = Object.fromEntries(scores.map((s) => [s.model_name, s]));

  return (
    <div className="space-y-6">
      <Link href="/" className="text-xs text-faint hover:text-signal">
        ← all themes
      </Link>

      {/* header */}
      <div className="panel px-4 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-xl font-semibold">{theme.name}</h1>
              <DataModeBadge mode={theme.data_mode} />
            </div>
            <p className="mt-1 max-w-3xl text-sm text-muted">{theme.summary}</p>
            <div className="mt-3">
              <MaturityRail maturity={theme.maturity} stage={theme.maturity_stage} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-4">
            <Metric label="Trend" value={theme.trend_score} bar tone="signal" />
            <Metric label="Confidence" value={theme.confidence_score} bar tone="favour" />
            <Metric label="Opportunity" value={theme.opportunity_score} bar tone="warn" />
            <div>
              <div className="label">Market awareness</div>
              <div className="numeric text-lg text-muted">{theme.market_awareness}</div>
              <DataModeBadge mode={theme.market_awareness_mode} />
            </div>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-line pt-3 text-xs sm:grid-cols-5">
          <div>
            <span className="label">Evidence items</span>
            <div className="numeric text-muted">{detail.evidence_count}</div>
          </div>
          <div>
            <span className="label">Independent sources</span>
            <div className="numeric text-favour">{detail.independent_source_count}</div>
          </div>
          <div>
            <span className="label" title="independent sources ÷ evidence items">
              Amplification ratio
            </span>
            <div className="numeric text-warn">{detail.amplification_ratio.toFixed(2)}</div>
          </div>
          <div>
            <span className="label">Contradiction ratio</span>
            <div className="numeric text-against">
              {(detail.contradiction_ratio * 100).toFixed(0)}%
            </div>
          </div>
          <div>
            <span className="label">Companies mapped</span>
            <div className="numeric text-muted">{theme.company_count}</div>
          </div>
        </div>
        <p className="mt-3 text-[11px] text-faint">
          {detail.evidence_count} evidence items collapse to {detail.independent_source_count}{" "}
          independent confirmations. Repeated reporting of one announcement counts once.
        </p>
      </div>

      {/* why now */}
      {report && (
        <div className="grid gap-6 lg:grid-cols-3">
          <Panel title="What changed" subtitle="Measured against each signal's own baseline">
            <Findings lines={report.sections.what_changed} tone="muted" />
          </Panel>
          <Panel title="Why now" subtitle="Rate and breadth of evidence, not volume">
            <Findings lines={report.sections.why_now} tone="muted" />
          </Panel>
          <Panel title="Why it matters" subtitle="Transmission into public equities">
            <Findings lines={report.sections.why_it_matters} tone="muted" />
          </Panel>
        </div>
      )}

      {/* signals */}
      <Panel
        title="Signals and trends"
        subtitle="Observation window versus the prior baseline window"
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="label py-2 font-normal">Signal</th>
                <th className="label py-2 font-normal">Category</th>
                <th className="label py-2 text-right font-normal">Window level</th>
                <th className="label py-2 text-right font-normal">Baseline level</th>
                <th className="label py-2 text-right font-normal">Acceleration</th>
                <th className="label py-2 text-right font-normal">Frequency Δ</th>
                <th className="label py-2 text-right font-normal">Breadth Δ</th>
                <th className="label py-2 text-right font-normal">Direction</th>
              </tr>
            </thead>
            <tbody>
              {trends.map((trend) => (
                <tr key={trend.signal_key} className="border-b border-line/50">
                  <td className="py-2 pr-3 text-ink">{trend.signal_name}</td>
                  <td className="numeric py-2 pr-3 text-[11px] text-faint">{trend.category}</td>
                  <td className="numeric py-2 text-right text-signal">
                    {trend.observation_strength.toFixed(0)}
                  </td>
                  <td className="numeric py-2 text-right text-muted">
                    {trend.baseline_strength.toFixed(0)}
                  </td>
                  <td className="py-2 text-right">
                    <Delta value={trend.acceleration} />
                  </td>
                  <td className="py-2 text-right">
                    <Delta value={trend.frequency_change} />
                  </td>
                  <td className="py-2 text-right">
                    <Delta value={trend.independence_change} />
                  </td>
                  <td className="numeric py-2 text-right text-[11px] text-muted">
                    {trend.direction}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-[11px] text-faint">
          Level aggregates the whole window at once; acceleration compares the mean weekly
          bucket of the observation window against the baseline window, because only a rate is
          comparable between windows of different length.
        </p>
      </Panel>

      {/* value chain */}
      <Panel
        title="Value chain and company exposure"
        subtitle="Discovered by graph traversal — the path is the explanation"
      >
        {exposures.length === 0 ? (
          <Empty
            title="No company could be mapped."
            hint="The traversal found no company within its hop limit from this theme's anchors."
          />
        ) : (
          <div className="space-y-2">
            {exposures.map((exposure) => (
              <div key={exposure.company_key} className="border-b border-line/50 pb-2 last:border-0">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span className="numeric text-sm text-signal">
                    {exposure.ticker ?? exposure.company_key.toUpperCase()}
                  </span>
                  <span className="text-sm text-ink">{exposure.company_name}</span>
                  {exposure.is_fictional && (
                    <span
                      className="numeric text-[10px] text-demo"
                      title="Fictional issuer from the synthetic development universe"
                    >
                      FICTIONAL
                    </span>
                  )}
                  <span className="numeric text-[10px] text-muted">
                    {exposure.role.replace(/_/g, " ").toLowerCase()}
                  </span>
                  <span className="numeric text-[10px] text-faint">
                    order {exposure.order_of_effect}
                  </span>
                  <span className="ml-auto flex items-center gap-4">
                    <span className="numeric text-xs text-warn">
                      exposure {exposure.exposure_score.toFixed(0)}
                    </span>
                    <span className="numeric text-xs text-muted">
                      conf {exposure.confidence.toFixed(0)}
                    </span>
                  </span>
                </div>
                <p className="numeric mt-1 text-[11px] text-faint">{exposure.path}</p>
              </div>
            ))}
          </div>
        )}
      </Panel>

      {/* evidence for and against */}
      {report && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel
            title="Supporting evidence"
            subtitle="Counted by independent source, not by article"
          >
            <Findings lines={report.sections.supporting_evidence} tone="favour" />
          </Panel>
          <Panel
            title="Contradictory evidence"
            subtitle="Surfaced deliberately, never filtered to strengthen the narrative"
          >
            <Findings lines={report.sections.contradictory_evidence} tone="against" />
          </Panel>
        </div>
      )}

      {report && (
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel title="What the market already knows" subtitle="Coverage-derived estimate">
            <Findings lines={report.sections.market_awareness} tone="muted" />
          </Panel>
          <Panel
            title="What would invalidate this"
            subtitle="Expressed as measurable rules against named signals"
          >
            <ul className="space-y-2">
              {report.sections.invalidation_conditions.map((condition, index) => (
                <li key={index} className="border-l-2 border-warn/40 pl-3">
                  <p className="text-sm text-ink">{condition.condition}</p>
                  <p className="numeric mt-0.5 text-[11px] text-faint">
                    signal: {condition.signal} · threshold {condition.threshold}
                    {condition.current_value !== null &&
                      ` · current ${condition.current_value.toFixed(0)}`}
                  </p>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[11px] text-faint">
              These are recorded as rules so a later monitoring cycle can evaluate them
              automatically. Automatic evaluation is not implemented yet.
            </p>
          </Panel>
        </div>
      )}

      {report && report.sections.research_gaps.length > 0 && (
        <Panel
          title="Where we found nothing"
          subtitle="A gap in the research is not a gap in the world"
        >
          <Findings lines={report.sections.research_gaps} tone="muted" />
        </Panel>
      )}

      {/* evidence ledger */}
      <Panel
        title="Evidence ledger"
        subtitle={`${evidence?.length ?? 0} extracted items — expand any row for its full provenance chain`}
      >
        {!evidence || evidence.length === 0 ? (
          <Empty title="No evidence recorded." hint="Run the pipeline to populate the ledger." />
        ) : (
          <div className="max-h-[520px] overflow-y-auto pr-2">
            {evidence.map((item) => (
              <EvidenceRow key={item.id} item={item} />
            ))}
          </div>
        )}
      </Panel>

      {/* score decomposition */}
      <div className="grid gap-6 lg:grid-cols-3">
        {["trend_score", "confidence_score", "opportunity_score"].map((name) =>
          byModel[name] ? (
            <Panel
              key={name}
              title={name.replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())}
              subtitle={
                name === "opportunity_score"
                  ? "Research-opportunity quality — not a price forecast"
                  : name === "confidence_score"
                    ? "How well established the conclusion is"
                    : "How strong, and how changing, the theme is"
              }
            >
              <ScoreBreakdown score={byModel[name]} />
            </Panel>
          ) : null,
        )}
      </div>

      {/* trace */}
      <Panel
        title="Research trace"
        subtitle="What actually ran, read from the database"
        right={
          trace ? (
            <span className="numeric text-[11px] text-faint">
              run {trace.research_run_id.slice(0, 8)} · {trace.status} · stopped:{" "}
              {trace.stop_reason}
            </span>
          ) : undefined
        }
      >
        {!trace ? (
          <Empty
            title="No research run has been executed for this theme."
            hint="Run `make research` to plan, search and assemble a report. Until then there is no trace to show — this panel reflects the database, not a rendering."
          />
        ) : (
          <div className="space-y-4">
            <div>
              <span className="label">Plan</span>
              <p className="mt-1 text-xs text-muted">
                strategy{" "}
                <span className="numeric text-signal">{trace.plan_strategy}</span>
                {trace.agent_runs[0]?.model ? (
                  <>
                    {" "}
                    · model{" "}
                    <span className="numeric text-signal">{trace.agent_runs[0].model}</span>
                  </>
                ) : (
                  <span className="text-faint">
                    {" "}
                    · no language model was used; this plan was generated deterministically
                  </span>
                )}
              </p>
              <p className="mt-1 text-[11px] text-faint">{trace.plan_rationale}</p>
            </div>

            <div>
              <span className="label">Agents</span>
              <table className="mt-1 w-full text-xs">
                <tbody>
                  {trace.agent_runs.map((run, index) => (
                    <tr key={index} className="border-b border-line/40 last:border-0">
                      <td className="numeric py-1 text-muted">{run.agent_name}</td>
                      <td className="numeric py-1 text-faint">v{run.agent_version}</td>
                      <td className="numeric py-1 text-faint">{run.strategy}</td>
                      <td className="numeric py-1 text-favour">{run.status}</td>
                      <td className="numeric py-1 text-right text-faint">{run.duration_ms} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div>
              <span className="label">Research questions ({trace.questions.length})</span>
              <ul className="mt-1 space-y-1">
                {trace.questions.map((question, index) => (
                  <li key={index} className="text-xs">
                    <span className="numeric text-[10px] text-faint">
                      {question.dimension}
                    </span>{" "}
                    {question.seeks_counter_evidence && (
                      <span className="numeric text-[10px] text-against">COUNTER</span>
                    )}{" "}
                    <span className="text-muted">{question.question}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div>
              <span className="label">Searches executed ({trace.search_runs.length})</span>
              <table className="mt-1 w-full text-xs">
                <tbody>
                  {trace.search_runs.map((run, index) => (
                    <tr key={index} className="border-b border-line/40 last:border-0">
                      <td className="numeric py-1 text-faint">{run.provider_key}</td>
                      <td className="py-1">
                        <DataModeBadge mode={run.provider_mode} />
                      </td>
                      <td className="py-1 text-muted">{run.query}</td>
                      <td className="numeric py-1 text-right text-faint">
                        {run.result_count} hits · {run.new_document_count} new
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Not built yet" subtitle="Named rather than mocked">
          <div className="space-y-2">
            <NotImplemented
              feature="Bull / Bear / Skeptic agents"
              reason="only the Research Planner agent exists in this cycle; the case above is assembled from structured findings, not argued by agents"
            />
            <NotImplemented
              feature="Valuation and market expectations"
              reason="no market-data or fundamental-data provider is configured"
            />
            <NotImplemented
              feature="Catalysts, risk scoring, thesis monitoring, alerts"
              reason="scheduled for the next cycle"
            />
          </div>
        </Panel>
        {report && (
          <Panel title="Report provenance" subtitle="How this page was produced">
            <dl className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <dt className="text-faint">Generator</dt>
                <dd className="numeric text-muted">
                  {report.sections.provenance.generator} v
                  {report.sections.provenance.generator_version}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-faint">Pipeline version</dt>
                <dd className="numeric text-muted">
                  {report.sections.provenance.pipeline_version}
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-faint">As of</dt>
                <dd className="numeric text-muted">
                  {report.sections.provenance.as_of.slice(0, 19).replace("T", " ")}
                </dd>
              </div>
              {Object.entries(report.sections.provenance.provider_modes).map(
                ([capability, mode]) => (
                  <div key={capability} className="flex items-center justify-between">
                    <dt className="text-faint">{capability.replace("_", " ").toLowerCase()}</dt>
                    <dd>
                      <DataModeBadge mode={mode} />
                    </dd>
                  </div>
                ),
              )}
            </dl>
            <p className="mt-3 border-t border-line pt-2 text-[11px] text-faint">
              {report.sections.provenance.disclaimer}
            </p>
          </Panel>
        )}
      </div>
    </div>
  );
}
