"""API contract tests, driven through a real request cycle against a real database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from marketradar.api.app import create_app, get_session
from marketradar.demo.loader import seed_demo_universe
from marketradar.orchestration import run_pipeline
from marketradar.providers import build_default_registry
from marketradar.research.loop import run_research

pytestmark = pytest.mark.integration

AS_OF = datetime(2026, 9, 8, tzinfo=UTC)
SLUG = "ai-memory-demand"


@pytest.fixture
def client(session, settings):
    seed_demo_universe(session)
    registry = build_default_registry(settings)
    run_pipeline(session, as_of=AS_OF, settings=settings, registry=registry)
    run_research(session, theme_slug=SLUG, as_of=AS_OF, settings=settings, registry=registry)

    app = create_app()
    # The API reads through the test's transaction, so nothing it sees escapes the rollback.
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_providers_expose_unavailable_capabilities(client):
    payload = client.get("/providers").json()
    modes = {p["capability"]: p["mode"] for p in payload}
    assert modes["MARKET_DATA"] == "UNAVAILABLE"
    assert modes["NEWS_SEARCH"] == "DEMO"


def test_theme_list_is_ranked_and_carries_its_data_mode(client):
    themes = client.get("/themes").json()
    assert themes
    theme = themes[0]
    assert theme["slug"] == SLUG
    assert theme["data_mode"] == "DEMO"
    assert theme["maturity"] == "ACCELERATING"
    assert 0 <= theme["trend_score"] <= 100
    assert theme["confidence_score"] is not None
    assert theme["independent_source_count"] > 1


def test_theme_detail_returns_full_score_decomposition(client):
    detail = client.get(f"/themes/{SLUG}").json()
    scores = {s["model_name"]: s for s in detail["scores"]}
    assert set(scores) == {"trend_score", "confidence_score", "opportunity_score"}

    trend = scores["trend_score"]
    assert trend["components"]
    assert trend["unavailable_components"]
    unavailable = [c for c in trend["components"] if not c["available"]]
    assert unavailable and all(c["normalized"] is None for c in unavailable)
    assert all(c["explanation"] for c in trend["components"])


def test_theme_detail_reports_amplification(client):
    detail = client.get(f"/themes/{SLUG}").json()
    assert detail["evidence_count"] > detail["independent_source_count"]
    assert 0 < detail["amplification_ratio"] < 1


def test_exposures_expose_their_causal_path(client):
    detail = client.get(f"/themes/{SLUG}").json()
    exposures = detail["exposures"]
    assert exposures
    assert {e["order_of_effect"] for e in exposures} >= {1, 2}
    for exposure in exposures:
        assert exposure["hops"], "the path must be inspectable"
        assert exposure["is_fictional"] is True


def test_evidence_endpoint_returns_the_whole_provenance_chain(client):
    evidence = client.get(f"/themes/{SLUG}/evidence").json()
    assert evidence
    item = evidence[0]
    for field in (
        "document_url", "publisher", "source_type", "source_quality",
        "published_at", "event_at", "retrieved_at", "cluster_id", "rule_key",
        "extractor_version", "is_synthetic_source",
    ):
        assert field in item and item[field] is not None, field
    assert ".invalid" in item["document_url"]
    assert item["is_synthetic_source"] is True


def test_evidence_marks_cluster_origins_and_sizes(client):
    evidence = client.get(f"/themes/{SLUG}/evidence").json()
    amplified = [e for e in evidence if e["cluster_size"] > 1]
    assert amplified, "the corpus contains an amplified announcement"
    assert any(e["is_cluster_origin"] for e in amplified)
    assert any(not e["is_cluster_origin"] for e in amplified)


def test_report_endpoint_returns_labelled_sections(client):
    report = client.get(f"/themes/{SLUG}/report").json()
    assert report["generator"] == "deterministic_assembler"
    assert report["data_mode"] == "DEMO"
    sections = report["sections"]
    assert sections["contradictory_evidence"]
    assert sections["provenance"]["provider_modes"]["MARKET_DATA"] == "UNAVAILABLE"


def test_trace_endpoint_reflects_what_actually_ran(client):
    trace = client.get(f"/themes/{SLUG}/trace").json()
    assert trace["plan_strategy"] == "rule_based"
    assert trace["questions"]
    assert any(q["seeks_counter_evidence"] for q in trace["questions"])
    assert len(trace["agent_runs"]) == 1
    assert trace["agent_runs"][0]["model"] is None
    assert trace["search_runs"]
    assert trace["stop_reason"]


def test_trending_excludes_invented_issuers_by_default(client):
    """The single most important guarantee on this endpoint.

    The demo corpus is entirely fictional, so the default response must be EMPTY. A fake
    ticker in a ranked list of stocks is worse than no list: it looks like an answer.
    """
    assert client.get("/trending").json() == []

    included = client.get("/trending?include_fictional=true").json()
    assert included, "the demo corpus does form a theme with exposed companies"
    assert all(row["is_fictional"] for row in included)


def test_trending_carries_the_articles_behind_each_rating(client):
    rows = client.get("/trending?include_fictional=true").json()
    top = rows[0]
    assert top["headlines"], "a rating without the articles behind it is not checkable"
    for headline in top["headlines"]:
        assert headline["title"] and headline["url"]
        assert headline["publisher"]
        assert headline["is_synthetic"] is True  # the demo corpus is synthetic throughout
    # One row per article: several claims from one story must not read as several stories.
    urls = [h["url"] for h in top["headlines"]]
    assert len(urls) == len(set(urls))
    assert top["publisher_count"] == len({h["publisher"] for h in top["headlines"]})


def test_trending_states_its_reason_in_plain_language(client):
    top = client.get("/trending?include_fictional=true").json()[0]
    reason = top["headline_reason"]
    assert top["theme_name"] in reason
    # The reason is for a reader, so none of the internal vocabulary may leak into it.
    for jargon in ("DIRECT_BENEFICIARY", "order_of_effect", "exposure_score", "_"):
        assert jargon not in reason


def test_trending_counts_corroboration_in_clusters_not_publishers(client):
    """Syndication must never be presented as corroboration.

    The demo corpus deliberately contains one announcement carried by several outlets. The
    reason sentence must report the independent-cluster count, and say explicitly that the
    remaining publishers are running the same story.
    """
    top = client.get("/trending?include_fictional=true").json()[0]
    assert top["publisher_count"] > top["independent_reports"], (
        "this test needs a syndicated story to be meaningful"
    )
    reason = top["headline_reason"]
    assert f"{top['independent_reports']} sources reported it independently" in reason
    assert "running the same story" in reason
    assert f"{top['publisher_count']} publishers have reported" not in reason


def test_company_corroboration_is_not_the_themes_corroboration(client):
    """The two counts must stay separate.

    A company can sit inside a heavily corroborated theme on the strength of one article
    about itself. `independent_clusters` is the theme's number; `independent_reports` is the
    company's, and the sentence a reader sees must be built from the second.
    """
    rows = client.get("/trending?include_fictional=true").json()
    assert any(row["independent_reports"] != row["independent_clusters"] for row in rows)


def test_corroboration_counts_survive_truncating_the_headline_list(client):
    """Showing fewer articles must not change what is claimed about the evidence.

    Both counts are computed over every matching row, so a company backed by ten articles
    still reports ten articles' worth of corroboration when only six are displayed.
    """
    rows = client.get("/trending?include_fictional=true").json()
    top = max(rows, key=lambda r: r["publisher_count"])
    assert top["publisher_count"] >= len({h["publisher"] for h in top["headlines"]})
    assert top["independent_reports"] >= 1


def test_trending_ranks_companies_and_decomposes_every_rating(client):
    rows = client.get("/trending?include_fictional=true").json()
    assert rows, "the demo corpus forms a theme with exposed companies"

    assert [row["rank"] for row in rows] == list(range(1, len(rows) + 1))
    assert [row["score"] for row in rows] == sorted((r["score"] for r in rows), reverse=True)

    top = rows[0]
    assert 0.0 <= top["rating"] <= 10.0
    # The 0-10 figure must be the stored 0-100 score, not a separately computed number.
    assert top["rating"] == round(top["score"] / 10.0, 1)
    assert top["data_mode"] == "DEMO"
    assert top["direction"] in {"tailwind", "headwind"}

    # A rating is never served without the basis for it.
    assert top["components"], "every rating carries its decomposition"
    assert "price_confirmation" in top["unavailable_components"]
    unavailable = [c for c in top["components"] if c["key"] == "price_confirmation"]
    assert unavailable and unavailable[0]["available"] is False
    assert 0.0 < top["weight_coverage"] < 1.0


def test_trending_can_hide_headwinds_but_shows_them_by_default(client):
    default = client.get("/trending?include_fictional=true").json()
    tailwinds_only = client.get(
        "/trending?include_fictional=true&include_headwinds=false"
    ).json()
    assert all(row["direction"] == "tailwind" for row in tailwinds_only)
    assert len(tailwinds_only) <= len(default)


def test_trending_respects_its_limit(client):
    assert len(client.get("/trending?include_fictional=true&limit=2").json()) <= 2


def test_subjects_distinguish_discovered_topics_from_declared_ones(client):
    subjects = client.get("/subjects").json()
    assert subjects
    assert all(subject["cluster_count"] >= 0 for subject in subjects)
    assert [s["salience"] for s in subjects] == sorted(
        (s["salience"] for s in subjects), reverse=True
    )
    discovered = client.get("/subjects?discovered_only=true").json()
    assert all(subject["is_discovered"] for subject in discovered)


def test_unknown_theme_returns_a_structured_404(client):
    response = client.get("/themes/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_every_response_carries_a_request_id(client):
    response = client.get("/health")
    assert response.headers["x-request-id"]
