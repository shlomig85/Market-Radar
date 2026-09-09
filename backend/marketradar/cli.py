"""Market Radar command line.

Every stage of the vertical slice is runnable without the HTTP layer, which is what the
end-to-end test drives and what makes the pipeline debuggable in isolation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer
from sqlalchemy import select

from marketradar.config import get_settings
from marketradar.db.session import session_scope
from marketradar.demo.loader import seed_demo_universe
from marketradar.domain.enums import DataMode
from marketradar.domain.models import (
    Company,
    Event,
    EvidenceItem,
    ResearchReport,
    Score,
    SourceDocument,
    Theme,
    ThemeCompanyExposure,
)
from marketradar.logging import configure_logging
from marketradar.orchestration import run_pipeline
from marketradar.providers import build_default_registry

app = typer.Typer(help="Market Radar — investment intelligence pipeline", no_args_is_help=True)


def _bootstrap() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def providers() -> None:
    """Show the health and data mode of every provider capability."""
    _bootstrap()
    registry = build_default_registry()
    typer.echo(f"{'CAPABILITY':<16}{'MODE':<14}{'AVAILABLE':<11}DETAIL")
    typer.echo("-" * 100)
    for health in registry.health_report():
        typer.echo(
            f"{health.capability.value:<16}{health.mode.value:<14}"
            f"{str(health.available):<11}{health.detail}"
        )


@app.command()
def seed() -> None:
    """Load the fictional DEMO reference universe (industries, companies, graph edges)."""
    _bootstrap()
    with session_scope() as session:
        report = seed_demo_universe(session)
    typer.echo(
        f"Seeded {report.industries} industries, {report.companies} companies, "
        f"{report.securities} securities, {report.relationships} graph edges (DEMO)."
    )


@app.command()
def pipeline(
    as_of: str = typer.Option(
        None, help="ISO timestamp to run as-of. Defaults to now (UTC)."
    ),
) -> None:
    """Run ingest -> cluster -> evidence -> graph -> events -> signals -> trends -> scores."""
    _bootstrap()
    moment = (
        datetime.fromisoformat(as_of).replace(tzinfo=UTC)
        if as_of
        else datetime.now(tz=UTC)
    )
    with session_scope() as session:
        result = run_pipeline(session, as_of=moment)

    if result.retraction.anything_retracted:
        typer.echo(
            f"Retracted:  {result.retraction.evidence_retracted} evidence, "
            f"{result.retraction.events_retracted} events, "
            f"{result.retraction.edges_retracted} edges from a superseded extractor "
            f"({result.retraction.edges_uncited} edges left uncited)"
        )
    if result.subjects.considered:
        typer.echo(
            f"Subjects:   {result.subjects.created} new, "
            f"{result.subjects.updated} updated"
            + (
                f" — top: {', '.join(result.subjects.top_terms[:5])}"
                if result.subjects.top_terms
                else ""
            )
        )
    typer.echo(f"Documents:  seen={result.ingestion.documents_seen} "
               f"created={result.ingestion.documents_created} "
               f"skipped={result.ingestion.documents_skipped}")
    typer.echo(f"Clusters:   {result.ingestion.clusters_created} created")
    typer.echo(f"Evidence:   {result.ingestion.evidence_created} created")
    typer.echo(
        f"Graph:      {result.relationships.edges_created} edges created, "
        f"{result.relationships.edges_reinforced} reinforced "
        f"({result.relationships.evidence_created} relationship citations)"
    )
    typer.echo(f"Events:     {result.ingestion.events_created} created")
    typer.echo(f"Signals:    {result.signals_computed} computed")
    typer.echo(f"Trends:     {result.trends_computed} computed")
    typer.echo(f"Themes:     {', '.join(result.themes) or 'none formed'}")
    typer.echo(f"Exposures:  {result.exposures_created}")
    typer.echo(f"Scores:     {result.scores_created}")
    if result.notes:
        typer.echo("\nUnavailable capabilities (scores renormalised around them):")
        for note in result.notes:
            typer.echo(f"  - {note}")


@app.command()
def research(
    theme: str = typer.Argument(..., help="Theme slug, e.g. ai-memory-demand"),
    as_of: str = typer.Option(None, help="ISO timestamp to run as-of."),
) -> None:
    """Run the research loop for a theme and assemble its report."""
    _bootstrap()
    from marketradar.research.loop import run_research

    moment = (
        datetime.fromisoformat(as_of).replace(tzinfo=UTC)
        if as_of
        else datetime.now(tz=UTC)
    )
    with session_scope() as session:
        outcome = run_research(session, theme_slug=theme, as_of=moment)
        typer.echo(f"Research run:   {outcome.research_run_id} [{outcome.status.value}]")
        typer.echo(f"Plan strategy:  {outcome.plan_strategy}")
        typer.echo(f"Questions:      {outcome.question_count}")
        typer.echo(f"Searches:       {outcome.search_count}")
        typer.echo(f"New documents:  {outcome.new_documents}")
        typer.echo(f"Findings:       {outcome.finding_count} "
                   f"({outcome.contradicting_count} contradicting)")
        typer.echo(f"Stop reason:    {outcome.stop_reason}")
        typer.echo(f"Report:         {outcome.report_id}")


@app.command()
def show(
    theme: str = typer.Argument(..., help="Theme slug"),
    as_json: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Print a theme with its scores, exposures and score decomposition."""
    _bootstrap()
    with session_scope() as session:
        row = session.scalar(select(Theme).where(Theme.slug == theme))
        if row is None:
            typer.echo(f"No theme with slug '{theme}'.", err=True)
            raise typer.Exit(code=1)

        scores = session.scalars(
            select(Score)
            .where(Score.subject_type == "THEME", Score.subject_id == row.id)
            .order_by(Score.computed_at.desc())
        ).all()
        latest: dict[str, Score] = {}
        for score in scores:
            latest.setdefault(score.model_name, score)

        exposures = session.scalars(
            select(ThemeCompanyExposure)
            .where(ThemeCompanyExposure.theme_id == row.id)
            .order_by(ThemeCompanyExposure.exposure_score.desc())
        ).all()
        companies = {c.id: c for c in session.scalars(select(Company)).all()}

        if as_json:
            typer.echo(json.dumps({
                "slug": row.slug,
                "name": row.name,
                "maturity": row.maturity.value,
                "market_awareness": row.market_awareness.value,
                "data_mode": row.data_mode.value,
                "scores": {
                    name: {
                        "value": s.value,
                        "weight_coverage": s.weight_coverage,
                        "unavailable": (s.unavailable_components or {}).get("keys", []),
                    }
                    for name, s in latest.items()
                },
            }, indent=2))
            return

        typer.echo(f"\n{row.name}  [{row.data_mode.value}]")
        typer.echo("=" * 78)
        typer.echo(f"Maturity:         {row.maturity.value} (stage {row.maturity.stage})")
        typer.echo(f"Market awareness: {row.market_awareness.value} "
                   f"[{row.market_awareness_mode.value}]")
        typer.echo(f"Summary:          {row.summary}")
        for name, score in latest.items():
            dropped = (score.unavailable_components or {}).get("keys", [])
            typer.echo(f"\n{name}: {score.value}/100  "
                       f"(computed on {score.weight_coverage:.0%} of model weight)")
            for component in score.components:
                mark = " " if component.available else "!"
                shown = (
                    f"{component.normalized:6.1f}" if component.available else "  n/a"
                )
                typer.echo(
                    f"  {mark} {component.label:<28} {shown}  "
                    f"w={component.effective_weight:.3f}  {component.explanation[:72]}"
                )
            if dropped:
                typer.echo(f"    excluded: {', '.join(dropped)}")

        typer.echo("\nCompany exposure (value-chain traversal)")
        typer.echo("-" * 78)
        for exposure in exposures:
            company = companies.get(exposure.company_id)
            if company is None:
                continue
            typer.echo(
                f"  {company.ticker or company.key:<6} {company.name:<30} "
                f"{exposure.role.value:<26} order={exposure.order_of_effect} "
                f"exposure={exposure.exposure_score:5.1f} conf={exposure.confidence:5.1f}"
            )
            typer.echo(f"         path: {exposure.rationale}")


@app.command()
def stats() -> None:
    """Row counts across the intelligence tables."""
    _bootstrap()
    with session_scope() as session:
        for model in (SourceDocument, EvidenceItem, Event, Theme, ThemeCompanyExposure,
                      Score, ResearchReport):
            count = session.scalar(select(__import__("sqlalchemy").func.count()).select_from(model))
            typer.echo(f"{model.__tablename__:<28} {count}")




@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", help="Confirm destructive reset."),
) -> None:
    """Drop and recreate the schema, then reseed the DEMO universe (development only)."""
    _bootstrap()
    settings = get_settings()
    if settings.environment == "production":
        typer.echo("Refusing to reset a production database.", err=True)
        raise typer.Exit(code=1)
    if not yes:
        typer.echo("Refusing to reset without --yes.", err=True)
        raise typer.Exit(code=1)

    from alembic.config import Config

    from alembic import command

    config = Config("alembic.ini")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    with session_scope() as session:
        seed_demo_universe(session)
    typer.echo("Schema recreated and DEMO universe seeded.")




@app.command(name="live-check")
def live_check() -> None:
    """Probe the configured real providers and report exactly what came back.

    Intended to be run where the network is unrestricted. It performs the smallest real
    request each provider supports and prints the result, so "the live path works" is an
    observation rather than an expectation.
    """
    _bootstrap()
    settings = get_settings()
    registry = build_default_registry(settings)

    typer.echo(f"SEC User-Agent : {settings.sec_user_agent or '(not set — SEC will refuse)'}")
    typer.echo(f"Company provider: {settings.company_provider}")
    typer.echo(f"Filings provider: {settings.filings_provider}")
    typer.echo(f"Watchlist CIKs  : {', '.join(settings.sec_ciks) or '(none configured)'}")
    typer.echo(f"News provider   : {settings.news_provider}")
    typer.echo("-" * 78)

    failures = 0
    for health in registry.health_report():
        marker = "ok " if health.available else "!! "
        typer.echo(
            f"{marker}{health.capability.value:<14}{health.mode.value:<13}"
            f"verified={str(health.live_path_verified):<6}{health.detail[:60]}"
        )
        if not health.available and health.capability.value in {"COMPANY_DATA", "FILINGS"}:
            failures += 1

    company_provider = registry.company_data
    if company_provider.health().available:
        companies = company_provider.list_companies()
        typer.echo(f"\nCompany universe: {len(companies)} issuers")
        for company in companies[:5]:
            typer.echo(
                f"  {company.ticker or '-':<6} {company.name[:44]:<46}"
                f"cik={company.cik} mode={company.data_mode.value}"
            )

    news_provider = registry.news
    news_health = news_provider.health()
    if news_health.available and settings.news_provider in {"feeds", "rss"}:
        # Reported per feed, because "the news provider works" is not a useful statement
        # when ten independent publishers are involved and three of them are down.
        typer.echo("\nSubscribed feeds:")
        for source in news_provider.sources():
            typer.echo(
                f"  {source.key:<24}q={source.base_quality:<4}{source.source_class.value:<18}"
                f"{source.publisher[:34]}"
            )
        result = news_provider.get_recent_documents(limit=5)
        typer.echo(f"\nArticles fetched: {result.count} (mode {result.mode.value})")
        if result.detail:
            typer.echo(f"  {result.detail[:150]}")
        for document in result.documents:
            words = len(document.body_text.split())
            body_source = (document.payload or {}).get("body_source", "?")
            typer.echo(
                f"  {document.published_at.date()} {document.title[:52]:<54}"
                f"{words} words ({body_source})"
            )
            typer.echo(f"     {document.url[:96]}")
        if result.mode == DataMode.UNAVAILABLE:
            failures += 1

    filings_provider = registry.filings
    if filings_provider.health().available and settings.sec_ciks:
        from marketradar.providers.base import ProviderQuery

        result = filings_provider.search(ProviderQuery(text="", limit=3))
        typer.echo(f"\nFilings fetched: {result.count} (mode {result.mode.value})")
        for document in result.documents:
            words = len(document.body_text.split())
            typer.echo(f"  {document.published_at.date()} {document.title[:52]:<54}{words} words")
            typer.echo(f"     event_at={document.event_at.date() if document.event_at else '-'}"
                       f"  url={document.url[:64]}")

    typer.echo()
    if failures:
        typer.echo(f"{failures} real provider(s) unavailable — see the detail above.", err=True)
        raise typer.Exit(code=1)
    typer.echo("All configured real providers answered.")


@app.command(name="sync-companies")
def sync_companies_command() -> None:
    """Load the company universe from the configured company-data provider."""
    _bootstrap()
    from marketradar.ingestion.companies import sync_companies

    registry = build_default_registry()
    provider = registry.company_data
    health = provider.health()
    if not health.available:
        typer.echo(f"Company provider unavailable: {health.detail}", err=True)
        raise typer.Exit(code=1)

    with session_scope() as session:
        report = sync_companies(session, provider.list_companies(), health.mode)
    typer.echo(
        f"Companies: {report.created} created, {report.updated} updated, "
        f"{report.securities_created} securities ({report.mode.value})."
    )


@app.command()
def trending(
    limit: int = typer.Option(20, help="How many companies to show."),
    include_headwinds: bool = typer.Option(
        True, help="Include companies a rising theme works AGAINST."
    ),
) -> None:
    """Companies ranked by how strongly they are caught up in something that is changing.

    The rating is 0-10. It is an aggregation of things already measured and stored — theme
    trend, exposure path, independent corroboration — so every row can be taken apart into
    the reasons for it with `marketradar show`.
    """
    _bootstrap()
    from marketradar.scoring.company_trend import rate_companies

    with session_scope() as session:
        ratings = rate_companies(session, as_of=datetime.now(tz=UTC), persist=False)
        if not include_headwinds:
            ratings = [r for r in ratings if r.direction == "tailwind"]
        if not ratings:
            typer.echo(
                "No companies are rated. Either the pipeline has not run, or no theme has "
                "formed yet — which is the honest answer whenever nothing is accelerating."
            )
            return

        typer.echo(
            f"{'':<3}{'TICKER':<8}{'COMPANY':<32}{'RATING':<9}{'DIR':<10}{'MODE':<7}THEME"
        )
        typer.echo("-" * 104)
        for position, rating in enumerate(ratings[:limit], start=1):
            typer.echo(
                f"{position:<3}{rating.ticker or '-':<8}{rating.company_name[:31]:<32}"
                f"{rating.rating:<9.1f}{rating.direction:<10}{rating.data_mode.value:<7}"
                f"{rating.theme_name[:34]}"
            )
        typer.echo("-" * 104)

        demo = sum(1 for r in ratings[:limit] if r.data_mode.value == "DEMO")
        if demo:
            typer.echo(
                f"{demo} of these rest on the synthetic DEMO corpus — fictional issuers, "
                "not investable. Run with real providers configured for live ratings."
            )
        typer.echo(
            "Rating 0-10 from theme trend, exposure and independent corroboration. "
            "Price confirmation is UNAVAILABLE (no market-data provider), so its weight is "
            "redistributed rather than guessed."
        )


@app.command()
def subjects(
    limit: int = typer.Option(30, help="Maximum subjects to print."),
    discovered_only: bool = typer.Option(False, help="Hide the built-in lexicon subjects."),
) -> None:
    """Show what the corpus turned out to be about.

    A subject marked ``found`` was discovered from the documents; ``declared`` means it was
    typed into the built-in lexicon. The distinction is printed because the system must
    never claim to have discovered a topic somebody gave it.
    """
    _bootstrap()
    from marketradar.domain.models import Subject

    with session_scope() as session:
        query = select(Subject).order_by(Subject.salience.desc()).limit(limit)
        if discovered_only:
            query = query.where(Subject.is_discovered.is_(True))
        rows = session.scalars(query).all()
        if not rows:
            typer.echo("No subjects yet — run the pipeline first.")
            return

        typer.echo(
            f"{'SUBJECT':<34}{'ORIGIN':<11}{'CLUSTERS':<10}{'EMERGE':<9}{'SALIENCE':<10}FIRST SEEN"
        )
        typer.echo("-" * 92)
        for row in rows:
            origin = "found" if row.is_discovered else "declared"
            typer.echo(
                f"{row.term[:33]:<34}{origin:<11}{row.cluster_count:<10}"
                f"{row.emergence:<9.1f}{row.salience:<10.1f}{row.first_seen_at.date()}"
            )
        typer.echo("-" * 92)
        found = sum(1 for row in rows if row.is_discovered)
        typer.echo(
            f"{found} discovered, {len(rows) - found} declared. "
            "A subject must be supported by several INDEPENDENT clusters, so a single "
            "syndicated story cannot create one."
        )


@app.command()
def feeds() -> None:
    """Probe every subscribed feed and report what each one actually returned.

    Feed URLs rot: publishers move endpoints, drop RSS, or put a wall in front of it. A
    dead feed contributes nothing and, without this, contributes nothing *silently* — which
    looks identical to a quiet news day. This turns that into a five-second check.
    """
    _bootstrap()
    settings = get_settings()
    if settings.news_provider not in {"feeds", "rss"}:
        typer.echo(
            f"News provider is '{settings.news_provider}'. "
            "Set MARKETRADAR_NEWS_PROVIDER=feeds to use subscribed feeds.",
            err=True,
        )
        raise typer.Exit(code=1)

    probes = build_default_registry(settings).news.probe()

    typer.echo(f"{'FEED':<24}{'QUALITY':<9}{'ITEMS':<7}{'NEWEST':<12}STATUS")
    typer.echo("-" * 88)
    for probe in probes:
        newest = probe.newest.date().isoformat() if probe.newest else "-"
        items = str(probe.item_count) if probe.reachable else "-"
        # Not truncated: "Client error '4" hides whether a feed is 404 (wrong URL, replace
        # it) or 403 (blocked, fix the User-Agent), which are opposite remedies.
        typer.echo(
            f"{probe.feed.key:<24}{probe.feed.base_quality:<9}{items:<7}{newest:<12}"
            f"{probe.status}"
        )

    reachable = sum(1 for probe in probes if probe.reachable)
    typer.echo("-" * 88)
    typer.echo(f"{reachable}/{len(probes)} feeds reachable.")
    if reachable == 0:
        typer.echo(
            "No feed answered. Check network access, then MARKETRADAR_FEED_USER_AGENT — "
            "some publishers refuse requests that do not identify themselves.",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command()
def graph(
    company: str = typer.Option(None, help="Only edges touching this company key."),
    limit: int = typer.Option(40, help="Maximum edges to print."),
) -> None:
    """Show knowledge-graph edges and the evidence each one rests on.

    An edge with no citation is a claim nobody can check, so this prints the citation column
    unconditionally: ``(no evidence — hand-entered)`` is the honest rendering of a seeded
    edge, and it should be visibly rarer over time as real filings are ingested.
    """
    _bootstrap()
    from marketradar.domain.models import EntityRelationship

    with session_scope() as session:
        query = select(EntityRelationship).order_by(
            EntityRelationship.source_entity_key, EntityRelationship.target_entity_key
        )
        if company:
            query = query.where(
                (EntityRelationship.source_entity_key == company)
                | (EntityRelationship.target_entity_key == company)
            )
        edges = session.scalars(query.limit(limit)).all()
        if not edges:
            typer.echo("No edges." if not company else f"No edges touching {company}.")
            return

        # Edges address companies by key, which for real issuers is a zero-padded CIK. A
        # bare "sec-0002120882 SUPPLIES sec-0001045810" is unauditable — the reader cannot
        # tell a correct edge from a wrong one without looking up two CIKs by hand, which
        # is exactly what went unnoticed in the first live run.
        names = {c.key: c.name for c in session.scalars(select(Company)).all()}

        def label(key: str) -> str:
            name = names.get(key)
            return f"{name} [{key}]" if name else key

        cited = 0
        for edge in edges:
            typer.echo(
                f"{label(edge.source_entity_key)} --[{edge.relationship_type.value} "
                f"w={edge.weight:.2f} c={edge.confidence:.2f} {edge.data_mode.value}]--> "
                f"{label(edge.target_entity_key)}"
            )
            evidence = (
                session.get(EvidenceItem, edge.evidence_id) if edge.evidence_id else None
            )
            if evidence is None:
                typer.echo("    (no evidence — hand-entered)")
                continue
            cited += 1
            document = session.get(SourceDocument, evidence.document_id)
            excerpt = " ".join(evidence.excerpt.split())
            typer.echo(f'    "{excerpt[:160]}"')
            if document is not None:
                typer.echo(f"    {document.url}")

        typer.echo(f"\n{cited}/{len(edges)} edges shown carry evidence.")


if __name__ == "__main__":
    app()
