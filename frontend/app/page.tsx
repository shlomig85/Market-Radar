import Link from "next/link";

import { api } from "@/lib/api";
import {
  DataModeBadge,
  Delta,
  Empty,
  MaturityRail,
  Metric,
  NotImplemented,
  Panel,
} from "@/components/primitives";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const [themes, trending] = await Promise.all([api.themes(), api.trending(8)]);

  if (!themes) {
    return (
      <Empty
        title="The API is not reachable."
        hint="Start it with `make api`, then reload. Nothing is cached, so this page reflects live backend state."
      />
    );
  }

  if (themes.length === 0) {
    return (
      <Empty
        title="No themes have formed yet."
        hint="Run `make seed && make pipeline` to ingest the development corpus. A theme forms only when signals accelerate against their own baseline — an empty dashboard means nothing is changing, which is a valid answer."
      />
    );
  }

  const accelerating = [...themes].sort(
    (a, b) => (b.top_acceleration ?? 0) - (a.top_acceleration ?? 0),
  );
  const underappreciated = themes.filter(
    (t) => t.maturity_stage <= 3 && (t.confidence_score ?? 0) >= 50,
  );

  return (
    <div className="space-y-6">
      {trending && trending.length > 0 && (
        <Panel
          title="Trending stocks"
          subtitle="Companies rated 0-10 on how strongly they are caught up in something that is changing"
          right={
            <Link href="/trending" className="text-xs text-signal hover:underline">
              Full list, with the reasons &rarr;
            </Link>
          }
        >
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            {trending.map((company) => (
              <Link
                key={company.company_key}
                href={`/themes/${company.theme_slug}`}
                className="flex items-baseline gap-3 border-b border-line/50 py-2 last:border-0 hover:bg-raised/60"
              >
                <span className="numeric w-14 text-sm text-ink">
                  {company.ticker ?? "\u2014"}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm text-muted">
                  {company.company_name}
                  <DataModeBadge mode={company.data_mode} />
                </span>
                <span
                  className={`numeric text-sm font-semibold ${
                    company.direction === "headwind" ? "text-against" : "text-signal"
                  }`}
                  title={
                    company.direction === "headwind"
                      ? `The rise of ${company.theme_name} works AGAINST this company`
                      : `Exposed to ${company.theme_name}`
                  }
                >
                  {company.rating.toFixed(1)}
                </span>
              </Link>
            ))}
          </div>
        </Panel>
      )}

      <div>
        <h1 className="text-lg font-semibold tracking-wide">What changed</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Themes ranked by research priority — opportunity weighted by confidence. A theme
          appears here only when its underlying signals are running above their own historical
          baseline, not because a subject is widely covered.
        </p>
      </div>

      <Panel
        title="Emerging themes"
        subtitle={`${themes.length} theme${themes.length === 1 ? "" : "s"} formed from measured signal acceleration`}
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b border-line text-left">
                <th className="label py-2 font-normal">Theme</th>
                <th className="label py-2 font-normal">Maturity</th>
                <th className="label py-2 text-right font-normal">Trend</th>
                <th className="label py-2 text-right font-normal">Confidence</th>
                <th className="label py-2 text-right font-normal">Opportunity</th>
                <th className="label py-2 text-right font-normal">Accel.</th>
                <th className="label py-2 text-right font-normal">Ind. sources</th>
                <th className="label py-2 text-right font-normal">Companies</th>
                <th className="label py-2 text-right font-normal">Awareness</th>
              </tr>
            </thead>
            <tbody>
              {themes.map((theme) => (
                <tr key={theme.slug} className="border-b border-line/60 hover:bg-raised/60">
                  <td className="py-3 pr-4">
                    <Link href={`/themes/${theme.slug}`} className="group">
                      <span className="font-medium text-ink group-hover:text-signal">
                        {theme.name}
                      </span>
                      <DataModeBadge mode={theme.data_mode} />
                      <p className="mt-0.5 max-w-md text-xs text-muted">{theme.summary}</p>
                    </Link>
                  </td>
                  <td className="py-3 pr-4">
                    <MaturityRail maturity={theme.maturity} stage={theme.maturity_stage} />
                  </td>
                  <td className="numeric py-3 text-right text-signal">
                    {theme.trend_score?.toFixed(0) ?? "n/a"}
                  </td>
                  <td className="numeric py-3 text-right text-favour">
                    {theme.confidence_score?.toFixed(0) ?? "n/a"}
                  </td>
                  <td className="numeric py-3 text-right text-warn">
                    {theme.opportunity_score?.toFixed(0) ?? "n/a"}
                  </td>
                  <td className="py-3 text-right">
                    <Delta value={theme.top_acceleration} />
                  </td>
                  <td className="numeric py-3 text-right text-muted">
                    {theme.independent_source_count}
                  </td>
                  <td className="numeric py-3 text-right text-muted">{theme.company_count}</td>
                  <td className="py-3 text-right">
                    <div className="numeric text-xs text-muted">{theme.market_awareness}</div>
                    <DataModeBadge
                      mode={theme.market_awareness_mode}
                      title="Provenance of the market-awareness estimate specifically"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid gap-6 lg:grid-cols-3">
        <Panel title="Fastest accelerating" subtitle="Largest move against own baseline">
          {accelerating.slice(0, 5).map((theme) => (
            <Link
              key={theme.slug}
              href={`/themes/${theme.slug}`}
              className="flex items-center justify-between border-b border-line/50 py-2 last:border-0 hover:text-signal"
            >
              <span className="text-sm">{theme.name}</span>
              <Delta value={theme.top_acceleration} />
            </Link>
          ))}
        </Panel>

        <Panel
          title="Well evidenced, not yet consensus"
          subtitle="Confidence ≥ 50 and maturity at or below accelerating"
        >
          {underappreciated.length === 0 ? (
            <Empty
              title="Nothing qualifies right now."
              hint="Themes appear here once they are corroborated but still short of mainstream coverage."
            />
          ) : (
            underappreciated.slice(0, 5).map((theme) => (
              <Link
                key={theme.slug}
                href={`/themes/${theme.slug}`}
                className="flex items-center justify-between border-b border-line/50 py-2 last:border-0 hover:text-signal"
              >
                <span className="text-sm">{theme.name}</span>
                <span className="numeric text-xs text-muted">
                  conf {theme.confidence_score?.toFixed(0)} · {theme.maturity}
                </span>
              </Link>
            ))
          )}
        </Panel>

        <Panel title="Not built yet" subtitle="Named rather than mocked">
          <div className="space-y-2">
            <NotImplemented
              feature="Thesis changes"
              reason="continuous monitoring and thesis diffing arrive with the monitoring cycle"
            />
            <NotImplemented
              feature="Upcoming catalysts"
              reason="the catalyst engine is not implemented; no forward events are scored"
            />
            <NotImplemented
              feature="Portfolio impact"
              reason="portfolio import is out of scope for this cycle"
            />
          </div>
        </Panel>
      </div>
    </div>
  );
}
