import Link from "next/link";

import { api, type ScoreComponent, type TrendingCompany } from "@/lib/api";
import { DataModeBadge, Empty, Panel } from "@/components/primitives";

export const dynamic = "force-dynamic";

/**
 * The 0-10 rating, drawn.
 *
 * Ten segments, not a continuous bar: the rating is reported to one decimal because the
 * inputs justify no more, and a smooth bar would suggest a precision the number does not
 * have. A headwind rating is drawn in the "against" colour so the two directions can never
 * be confused at a glance.
 */
function RatingBar({ rating, direction }: { rating: number; direction: string }) {
  const filled = Math.round(rating);
  const tone = direction === "headwind" ? "bg-against" : "bg-signal";
  return (
    <span className="flex items-center gap-2">
      <span
        className={`numeric w-9 text-right text-base font-semibold ${
          direction === "headwind" ? "text-against" : "text-signal"
        }`}
      >
        {rating.toFixed(1)}
      </span>
      <span className="flex gap-[2px]" aria-hidden>
        {Array.from({ length: 10 }, (_, i) => (
          <span
            key={i}
            className={`h-3 w-1.5 rounded-[1px] ${i < filled ? tone : "bg-line"}`}
          />
        ))}
      </span>
    </span>
  );
}

/** One component of the rating, with the weight it actually carried. */
function ComponentRow({ component }: { component: ScoreComponent }) {
  if (!component.available) {
    return (
      <div className="border-l-2 border-line py-1.5 pl-3">
        <div className="flex items-baseline gap-2">
          <span className="text-xs text-faint">{component.label}</span>
          <DataModeBadge mode="UNAVAILABLE" />
        </div>
        <p className="mt-0.5 text-xs text-faint">{component.explanation}</p>
      </div>
    );
  }
  return (
    <div className="border-l-2 border-line py-1.5 pl-3">
      <div className="flex items-baseline gap-2">
        <span className="text-xs text-ink">{component.label}</span>
        <span className="numeric text-xs text-muted">
          {component.normalized?.toFixed(0) ?? "n/a"}/100
        </span>
        <span className="numeric text-[11px] text-faint">
          × {(component.effective_weight * 100).toFixed(0)}% weight ={" "}
          {component.contribution.toFixed(1)} pts
        </span>
      </div>
      <p className="mt-0.5 text-xs text-muted">{component.explanation}</p>
    </div>
  );
}

function Row({ company }: { company: TrendingCompany }) {
  return (
    <details className="group border-b border-line/60 last:border-0">
      <summary className="grid cursor-pointer list-none grid-cols-[3rem_5rem_1fr_11rem_5rem] items-center gap-3 py-3 hover:bg-raised/60">
        <span className="flex items-baseline gap-1.5">
          <span className="text-[9px] text-signal transition-transform group-open:rotate-90">
            &#9656;
          </span>
          <span className="numeric text-xs text-faint">{company.rank}</span>
        </span>
        <span className="numeric text-sm text-ink">{company.ticker ?? "—"}</span>
        <span className="min-w-0">
          <span className="truncate text-sm text-ink">{company.company_name}</span>
          <DataModeBadge mode={company.data_mode} />
          <span className="mt-0.5 block truncate text-xs text-muted">
            {company.direction === "headwind" ? "Headwind from " : "Tailwind from "}
            <span className="text-ink">{company.theme_name}</span>
            {company.theme_count > 1 && ` · exposed to ${company.theme_count} themes`}
          </span>
        </span>
        <RatingBar rating={company.rating} direction={company.direction} />
        <span
          className={`label text-right ${
            company.direction === "headwind" ? "text-against" : "text-faint"
          }`}
        >
          {company.direction}
        </span>
      </summary>

      <div className="grid gap-4 bg-raised/30 px-4 py-3 lg:grid-cols-[1fr_20rem]">
        <div className="space-y-1">
          <p className="label">How the {company.rating.toFixed(1)} was reached</p>
          {company.components.map((component) => (
            <ComponentRow key={component.key} component={component} />
          ))}
          <p className="pt-1 text-[11px] text-faint">
            Components are weighted to {(company.weight_coverage * 100).toFixed(0)}% coverage.
            {company.unavailable_components.length > 0 && (
              <>
                {" "}
                {company.unavailable_components.join(", ")} could not be computed, so the
                weight was redistributed across the rest rather than estimated.
              </>
            )}
          </p>
        </div>

        <div className="space-y-2 text-xs">
          <div>
            <p className="label">Why this company</p>
            <p className="mt-1 text-muted">{company.rationale}</p>
            <p className="mt-1 text-faint">
              Order {company.order_of_effect} in the value chain · exposure{" "}
              <span className="numeric">{company.exposure_score.toFixed(0)}</span>/100 ·{" "}
              <span className="numeric">{company.independent_clusters}</span> independent
              evidence cluster{company.independent_clusters === 1 ? "" : "s"}
            </p>
          </div>
          <Link
            href={`/themes/${company.theme_slug}`}
            className="inline-block text-signal hover:underline"
          >
            Open {company.theme_name} →
          </Link>
        </div>
      </div>
    </details>
  );
}

export default async function TrendingPage() {
  const [companies, subjects] = await Promise.all([api.trending(40), api.subjects(24)]);

  if (!companies) {
    return (
      <Empty
        title="The API is not reachable."
        hint="Start it with `make api`, then reload. Nothing is cached, so this page reflects live backend state."
      />
    );
  }

  if (companies.length === 0) {
    return (
      <Empty
        title="No company is rated yet."
        hint="A company is rated only when it sits on the value chain of a theme that is measurably accelerating. Run `make pipeline` to ingest and score. An empty list means nothing qualified — which is a valid answer, not a failure."
      />
    );
  }

  const demo = companies.filter((c) => c.data_mode === "DEMO").length;
  const discovered = (subjects ?? []).filter((s) => s.is_discovered);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-wide">Trending stocks</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Companies rated 0&ndash;10 on how strongly they are caught up in something that is
          changing. The rating is an aggregation of things already measured &mdash; theme
          trend, acceleration, position on the value chain, independent corroboration &mdash;
          so every row opens into the reasons for it. It ranks research priority, not
          expected return.
        </p>
      </div>

      <Panel
        title="Ranked by trend rating"
        subtitle={`${companies.length} rated compan${companies.length === 1 ? "y" : "ies"} · strongest theme decides each rating, additional themes add a capped bonus`}
        right={
          demo > 0 ? (
            <span className="flex items-center gap-2 text-xs text-demo">
              <DataModeBadge mode="DEMO" />
              {demo} of {companies.length} rest on the synthetic corpus
            </span>
          ) : undefined
        }
      >
        <div className="grid grid-cols-[3rem_5rem_1fr_11rem_5rem] gap-3 border-b border-line pb-2">
          <span className="label">#</span>
          <span className="label">Ticker</span>
          <span className="label">Company</span>
          <span className="label">Rating 0&ndash;10</span>
          <span className="label text-right">Dir.</span>
        </div>
        {companies.map((company) => (
          <Row key={company.company_key} company={company} />
        ))}
        <p className="pt-3 text-[11px] leading-relaxed text-faint">
          A <span className="text-against">headwind</span> row is a company the rising theme
          works <em>against</em> &mdash; a competitor or a substitute. It is listed because it
          is genuinely exposed, and labelled because presenting it alongside beneficiaries
          without the distinction would be the most misleading thing this list could do.
          Price confirmation is not computed: no market-data provider is configured, so its
          weight is redistributed rather than guessed.
        </p>
      </Panel>

      <Panel
        title="What the corpus turned out to be about"
        subtitle="Topics mined from the documents themselves, not from a vocabulary typed into the system"
      >
        {subjects === null || subjects.length === 0 ? (
          <Empty
            title="No subjects have been discovered yet."
            hint="Subject discovery runs as part of `make pipeline`. A term qualifies only when several INDEPENDENT evidence clusters support it, so one syndicated story cannot mint a topic."
          />
        ) : (
          <>
            <div className="flex flex-wrap gap-1.5">
              {subjects.map((subject) => (
                <span
                  key={subject.key}
                  className="border border-line bg-raised px-2 py-1 text-xs"
                  title={`${subject.cluster_count} independent clusters · ${subject.document_count} documents · salience ${subject.salience.toFixed(0)} · first seen ${subject.first_seen_at.slice(0, 10)}`}
                >
                  <span className={subject.is_discovered ? "text-ink" : "text-muted"}>
                    {subject.term}
                  </span>
                  {subject.is_discovered ? (
                    <span className="numeric ml-1.5 text-faint">{subject.cluster_count}</span>
                  ) : (
                    <span className="ml-1.5 text-[10px] uppercase tracking-wider text-faint">
                      declared
                    </span>
                  )}
                </span>
              ))}
            </div>
            <p className="pt-3 text-[11px] text-faint">
              {discovered.length} discovered, {subjects.length - discovered.length} declared
              (shown dimmed). The number on each chip is the count of independent evidence
              clusters behind it &mdash; not the document count, because ten documents from
              one origin are one confirmation. A declared subject carries no such count: it
              came from the built-in lexicon and was never required to earn one.
            </p>
          </>
        )}
      </Panel>
    </div>
  );
}
