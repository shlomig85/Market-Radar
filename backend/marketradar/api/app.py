"""FastAPI application.

The HTTP layer is thin on purpose: validate, call a service, serialise. It holds no
business logic, so the pipeline and the tests exercise the same code the API does.

Cycle 1 ships **no authentication** (ADR-012). This is a single-tenant development surface
and must not be exposed publicly until the auth boundary exists in cycle 2. That is stated
here, in the README and in the cycle report rather than being papered over with a token
check that has never been threat-modelled.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from marketradar import __version__
from marketradar.api import services
from marketradar.api.schemas import (
    EvidenceOut,
    ProviderHealthOut,
    ReportOut,
    ResearchTraceOut,
    SubjectOut,
    ThemeDetailOut,
    ThemeSummaryOut,
    TrendingCompanyOut,
)
from marketradar.config import get_settings
from marketradar.db.session import get_sessionmaker
from marketradar.errors import MarketRadarError
from marketradar.logging import bind_context, clear_context, configure_logging, get_logger
from marketradar.providers import build_default_registry

log = get_logger(__name__)


def get_session() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    log.info("api.start", version=__version__, environment=settings.environment)
    yield
    log.info("api.stop")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Market Radar API",
        version=__version__,
        description=(
            "Emerging-theme investment intelligence. Research output only: nothing here is "
            "investment advice, and scores rank research priority rather than expected return."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        bind_context(request_id=request_id, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            clear_context()
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(MarketRadarError)
    async def domain_error_handler(request: Request, exc: MarketRadarError) -> JSONResponse:
        # One place maps domain errors to HTTP. Nothing is swallowed; everything is logged.
        log.warning("api.domain_error", code=exc.code, message=exc.message, **exc.context)
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})

    # ---------------------------------------------------------------- routes
    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/providers", response_model=list[ProviderHealthOut], tags=["system"])
    def providers() -> list[ProviderHealthOut]:
        """Provider health. The UNAVAILABLE entries are the point: they tell the UI which
        parts of the analysis had no data behind them."""
        registry = build_default_registry()
        return [
            ProviderHealthOut(
                capability=h.capability.value,
                provider_key=h.provider_key,
                available=h.available,
                mode=h.mode.value,
                detail=h.detail,
                live_path_verified=h.live_path_verified,
                checked_at=h.checked_at,
            )
            for h in registry.health_report()
        ]

    @app.get("/trending", response_model=list[TrendingCompanyOut], tags=["trending"])
    def trending(
        limit: int = 25,
        include_headwinds: bool = True,
        include_fictional: bool = False,
        session: Session = Depends(get_session),
    ) -> list[TrendingCompanyOut]:
        """Companies ranked 0-10 on how strongly they are caught up in something changing.

        Every row carries the articles behind it and its own score decomposition, so the
        list can be taken apart into the reasons for it rather than read as an oracle.
        Fictional issuers from the synthetic corpus are excluded unless asked for.
        """
        return services.list_trending(
            session,
            limit=limit,
            include_headwinds=include_headwinds,
            include_fictional=include_fictional,
        )

    @app.get("/subjects", response_model=list[SubjectOut], tags=["trending"])
    def subjects(
        limit: int = 40,
        discovered_only: bool = False,
        session: Session = Depends(get_session),
    ) -> list[SubjectOut]:
        """What the corpus turned out to be about, mined from the documents themselves."""
        return services.list_subjects(session, limit=limit, discovered_only=discovered_only)

    @app.get("/themes", response_model=list[ThemeSummaryOut], tags=["themes"])
    def themes(session: Session = Depends(get_session)) -> list[ThemeSummaryOut]:
        return services.list_themes(session)

    @app.get("/themes/{slug}", response_model=ThemeDetailOut, tags=["themes"])
    def theme(slug: str, session: Session = Depends(get_session)) -> ThemeDetailOut:
        return services.theme_detail(session, slug)

    @app.get("/themes/{slug}/evidence", response_model=list[EvidenceOut], tags=["themes"])
    def evidence(
        slug: str, limit: int = 200, session: Session = Depends(get_session)
    ) -> list[EvidenceOut]:
        return services.theme_evidence(session, slug, limit=limit)

    @app.get("/themes/{slug}/report", response_model=ReportOut, tags=["research"])
    def report(slug: str, session: Session = Depends(get_session)) -> ReportOut:
        return services.theme_report(session, slug)

    @app.get("/themes/{slug}/trace", response_model=ResearchTraceOut, tags=["research"])
    def trace(slug: str, session: Session = Depends(get_session)) -> ResearchTraceOut:
        """What actually ran: agents, plans, queries and providers, from the database."""
        return services.theme_trace(session, slug)

    return app


app = create_app()
