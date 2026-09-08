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
    """Run ingest -> cluster -> evidence -> events -> signals -> trends -> themes -> scores."""
    _bootstrap()
    moment = (
        datetime.fromisoformat(as_of).replace(tzinfo=UTC)
        if as_of
        else datetime.now(tz=UTC)
    )
    with session_scope() as session:
        result = run_pipeline(session, as_of=moment)

    typer.echo(f"Documents:  seen={result.ingestion.documents_seen} "
               f"created={result.ingestion.documents_created} "
               f"skipped={result.ingestion.documents_skipped}")
    typer.echo(f"Clusters:   {result.ingestion.clusters_created} created")
    typer.echo(f"Evidence:   {result.ingestion.evidence_created} created")
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


if __name__ == "__main__":
    app()
