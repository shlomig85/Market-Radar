import Link from "next/link";

import {
  api,
  type Headline,
  type ScoreComponent,
  type TrendingCompany,
} from "@/lib/api";
import { DataModeBadge, Empty, Panel } from "@/components/primitives";

export const dynamic = "force-dynamic";

/** "3 days ago" reads faster than a date when the whole point is recency. */
function ago(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

/**
 * The 0-10 rating, drawn.
 *
 * Ten segments rather than a smooth bar: the rating is reported to one decimal because the
 * inputs justify no more, and a continuous bar would suggest a precision it does not have.
 */
function RatingBar({ rating, direction }: { rating: number; direction: string }) {
  const filled = Math.round(rating);
  const against = direction === "headwind";
  return (
    <div className="flex flex-col items-end gap-1">
      <span
        className={`numeric text-3xl font-semibold leading-none ${
          against ? "text-against" : "text-signal"
        }`}
      >
        {rating.toFixed(1)}
      </span>
      <span className="flex gap-[2px]" aria-hidden>
        {Array.from({ length: 10 }, (_, i) => (
          <span
            key={i}
            className={`h-1.5 w-2 rounded-[1px] ${
              i < filled ? (against ? "bg-against" : "bg-signal") : "bg-line"
            }`}
          />
        ))}
      </span>
    </div>
  );
}

/** One article, as a reader would want it: what, who, when, and a way to go read it. */
function HeadlineRow({ headline }: { headline: Headline }) {
  return (
    <a
      href={headline.url}
      target="_blank"
      rel="noreferrer noopener"
      className="block border-l-2 border-line py-1.5 pl-3 hover:border-signal"
    >
      <span className="text-sm text-ink hover:text-signal">{headline.title}</span>
      <span className="mt-0.5 block text-xs text-faint">
        {headline.publisher} · {ago(headline.published_at)}
        {headline.is_synthetic && " · SYNTHETIC"}
      </span>
    </a>
  );
}

/** One component of the rating. Only shown once a reader asks for the maths. */
function ComponentRow({ component }: { component: ScoreComponent }) {
  if (!component.available) {
    return (
      <div className="py-1">
        <span className="text-xs text-faint">{component.label}</span>
        <DataModeBadge mode="UNAVAILABLE" />
        <p className="mt-0.5 text-xs text-faint">{component.explanation}</p>
      </div>
    );
  }
  return (
    <div className="py-1">
      <span className="text-xs text-ink">{component.label}</span>
      <span className="numeric ml-2 text-xs text-muted">
        {component.normalized?.toFixed(0) ?? "n/a"}/100
      </span>
      <span className="numeric ml-2 text-[11px] text-faint">
        × {(component.effective_weight * 100).toFixed(0)}% = {component.contribution.toFixed(1)}{" "}
        pts
      </span>
      <p className="mt-0.5 text-xs text-muted">{component.explanation}</p>
    </div>
  );
}

function Card({ company }: { company: TrendingCompany }) {
  const direct = company.headlines.some((h) => h.about_company);

  return (
    <article className="border-b border-line/60 py-4 last:border-0">
      <div className="flex items-start gap-4">
        <span className="numeric w-6 pt-1 text-sm text-faint">{company.rank}</span>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="numeric text-lg font-semibold text-ink">
              {company.ticker ?? company.company_name}
            </span>
            {company.ticker && (
              <span className="text-sm text-muted">{company.company_name}</span>
            )}
            {company.direction === "headwind" && (
              <span className="border border-against/50 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-against">
                works against them
              </span>
            )}
            {company.is_fictional && (
              <span className="border border-demo/50 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-demo">
                invented — not a real company
              </span>
            )}
          </div>

          <p className="mt-1 max-w-2xl text-sm text-muted">{company.headline_reason}</p>

          {company.headlines.length > 0 && (
            <div className="mt-3 space-y-0.5">
              <p className="label">
                {direct ? "What was written about it" : "What was written about the theme"}
              </p>
              {company.headlines.map((headline) => (
                <HeadlineRow key={headline.url} headline={headline} />
              ))}
            </div>
          )}

          <details className="group mt-3">
            <summary className="label cursor-pointer list-none hover:text-signal">
              <span className="text-signal">+</span> How the {company.rating.toFixed(1)} was
              built
            </summary>
            <div className="mt-2 border-l-2 border-line pl-3">
              {company.components.map((component) => (
                <ComponentRow key={component.key} component={component} />
              ))}
              <p className="pt-1 text-[11px] text-faint">
                Weighted to {(company.weight_coverage * 100).toFixed(0)}% coverage.
                {company.unavailable_components.length > 0 && (
                  <>
                    {" "}
                    {company.unavailable_components.join(", ")} could not be computed, so the
                    weight was spread across the rest rather than estimated.
                  </>
                )}{" "}
                <Link href={`/themes/${company.theme_slug}`} className="text-signal">
                  Open {company.theme_name} →
                </Link>
              </p>
            </div>
          </details>
        </div>

        <RatingBar rating={company.rating} direction={company.direction} />
      </div>
    </article>
  );
}

export default async function TrendingPage({
  searchParams,
}: {
  searchParams: Promise<{ demo?: string }>;
}) {
  // `?demo=1` is the only way to see the synthetic corpus on this page. It exists so an
  // empty list is not a dead end while the pipeline is being set up — not as a display
  // mode, which is why it is a URL you have to type rather than a toggle you can nudge.
  const showDemo = (await searchParams).demo === "1";
  const [companies, providers] = await Promise.all([
    api.trending(30, showDemo),
    api.providers(),
  ]);

  if (!companies) {
    return (
      <Empty
        title="The API is not reachable."
        hint="Start it with `make api`, then reload. Nothing is cached, so this page reflects live backend state."
      />
    );
  }

  const news = providers?.find((p) => p.capability === "NEWS_SEARCH");
  const newsUnavailable = news?.mode === "UNAVAILABLE";
  const newsIsDemo = news?.mode === "DEMO";

  if (companies.length === 0) {
    return (
      <div className="space-y-4">
        <h1 className="text-lg font-semibold tracking-wide">Trending stocks</h1>
        {newsUnavailable ? (
          <Empty
            title="No news source is configured, so there is nothing to rank."
            hint="Set MARKETRADAR_FEED_USER_AGENT and MARKETRADAR_SEC_USER_AGENT to a contact string like 'Market Radar you@example.com' in .env, then run `make pipeline`. Publishers and the SEC both refuse anonymous requests, so nothing is fetched until you identify yourself."
          />
        ) : newsIsDemo ? (
          <>
            <Empty
              title="The synthetic corpus is loaded, and its companies are invented."
              hint="Invented issuers are excluded from this list on purpose — a fake ticker in a list of stocks is worse than an empty list. Set MARKETRADAR_NEWS_PROVIDER=feeds and a contact string in MARKETRADAR_FEED_USER_AGENT, then run `make pipeline` to rank real companies."
            />
            <Link href="/trending?demo=1" className="text-xs text-demo hover:underline">
              Show the invented companies anyway, to see the layout &rarr;
            </Link>
          </>
        ) : (
          <Empty
            title="Nothing qualifies right now."
            hint="A company appears here only when it sits on the value chain of a theme that is measurably accelerating against its own baseline. Run `make pipeline` if you have not, and check `make feeds` to see which publishers answered. An empty list is a valid answer, not a failure."
          />
        )}
      </div>
    );
  }

  const totalHeadlines = companies.reduce((n, c) => n + c.headlines.length, 0);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold tracking-wide">Trending stocks</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Ranked by how strongly each company is caught up in something that is measurably
          changing. Highest first. Every score comes with the articles that produced it —
          read those, not the number.
        </p>
      </div>

      <Panel
        title={`${companies.length} companies`}
        subtitle={`from ${totalHeadlines} articles across the subscribed publishers · rating 0–10`}
        right={
          showDemo || newsIsDemo ? (
            <span className="flex items-center gap-2 text-xs text-demo">
              <DataModeBadge mode="DEMO" />
              {showDemo
                ? "showing invented issuers — none of these is a real company"
                : "synthetic corpus — invented issuers are excluded from this list"}
            </span>
          ) : undefined
        }
      >
        {companies.map((company) => (
          <Card key={company.company_key} company={company} />
        ))}
      </Panel>

      <p className="px-1 text-[11px] leading-relaxed text-faint">
        This ranks <span className="text-muted">research priority</span>, not expected return:
        it says something is happening around a company, not that the stock is cheap. There is
        no price or valuation input at all — no market-data source is configured — so a high
        rating cannot tell an unnoticed move from one the market already priced in. Rows marked{" "}
        <span className="text-against">works against them</span> are companies the rising theme
        hurts; they rank highly because they are strongly exposed, not because they benefit.
      </p>
    </div>
  );
}
