# Market Radar — development entry points.
# `make help` lists everything.

SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
PY := python3

.DEFAULT_GOAL := help
.PHONY: help up down logs setup db-up db-down migrate migration seed pipeline research show \
        providers stats graph reset api web test test-unit test-integration test-e2e \
        lint typecheck check all docker-cli

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Run the whole stack in Docker, then open http://localhost:3000
	./scripts/start.sh

down: ## Stop the Docker stack
	docker compose down

logs: ## Follow the Docker stack logs
	docker compose logs -f

setup: ## Install backend and frontend dependencies
	$(PY) -m pip install -e "$(BACKEND)[dev]"
	cd $(FRONTEND) && npm install

db-up: ## Start PostgreSQL
	docker compose up -d postgres
	@echo "waiting for postgres..."
	@until docker compose exec -T postgres pg_isready -U marketradar >/dev/null 2>&1; do sleep 1; done
	@echo "postgres ready"

db-down: ## Stop PostgreSQL
	docker compose down

migrate: ## Apply database migrations
	cd $(BACKEND) && $(PY) -m alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add x"
	cd $(BACKEND) && $(PY) -m alembic revision --autogenerate -m "$(m)"

seed: ## Load the fictional DEMO reference universe
	cd $(BACKEND) && $(PY) -m marketradar.cli seed

pipeline: ## Run ingest -> events -> signals -> trends -> themes -> scores
	cd $(BACKEND) && $(PY) -m marketradar.cli pipeline

research: ## Run the research loop: make research theme=ai-memory-demand
	cd $(BACKEND) && $(PY) -m marketradar.cli research $(or $(theme),ai-memory-demand)

show: ## Print a theme with its score decomposition: make show theme=ai-memory-demand
	cd $(BACKEND) && $(PY) -m marketradar.cli show $(or $(theme),ai-memory-demand)

providers: ## Show provider health and data modes
	cd $(BACKEND) && $(PY) -m marketradar.cli providers

stats: ## Row counts across the intelligence tables
	cd $(BACKEND) && $(PY) -m marketradar.cli stats

graph: ## Show knowledge-graph edges and the evidence each one rests on
	cd $(BACKEND) && $(PY) -m marketradar.cli graph --limit $(or $(limit),40)

docker-cli: ## Run a CLI command in Docker against the stack's database: make docker-cli cmd="graph"
	docker compose --profile cli run --rm cli python -m marketradar.cli $(or $(cmd),stats)

reset: ## Drop, recreate and reseed the development database
	cd $(BACKEND) && $(PY) -m marketradar.cli reset --yes

api: ## Run the API on :8000
	cd $(BACKEND) && $(PY) -m uvicorn marketradar.api.app:app --reload --port 8000

web: ## Run the frontend on :3000
	cd $(FRONTEND) && npm run dev

test: ## Run the whole test suite (requires PostgreSQL)
	cd $(BACKEND) && $(PY) -m pytest -q

test-unit: ## Unit tests only (no database needed)
	cd $(BACKEND) && $(PY) -m pytest tests/unit -q

test-integration: ## Integration tests
	cd $(BACKEND) && $(PY) -m pytest tests/integration -q

test-e2e: ## End-to-end vertical slice
	cd $(BACKEND) && $(PY) -m pytest tests/e2e -q

lint: ## Lint the backend
	cd $(BACKEND) && $(PY) -m ruff check marketradar tests

typecheck: ## Typecheck both sides
	cd $(BACKEND) && $(PY) -m mypy marketradar || true
	cd $(FRONTEND) && npm run typecheck

check: lint test ## Lint and test

all: db-up migrate seed pipeline research ## Full local bring-up, from empty database to report
