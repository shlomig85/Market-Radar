#!/usr/bin/env bash
# Market Radar — bring the whole stack up on this machine.
#
#   ./scripts/start.sh            # containers (needs Docker)
#   ./scripts/start.sh --native   # host processes (needs Python 3.11+, Node 20+, PostgreSQL 16)
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--native" ]]; then
  # The codebase targets Python 3.11+ (datetime.UTC, PEP 604 unions at runtime). Without
  # this check the failure is an ImportError from deep inside a module, which points at
  # the wrong thing entirely. macOS ships 3.9 as `python3`.
  PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)' 2>/dev/null || echo 0)
  if [[ "$PY_OK" != "1" ]]; then
    FOUND=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "none")
    printf '%s\n' >&2 \
      "Python 3.11 or newer is required; found ${FOUND}." \
      "" \
      "  * macOS ships 3.9 as python3. Install a newer one (python.org, pyenv or brew)," \
      "    or simply use the container path, which bundles the right version:" \
      "        ./scripts/start.sh" \
      "  * To run a one-off command in the container instead:" \
      "        docker compose --profile cli run --rm cli python -m marketradar.cli <command>"
    exit 1
  fi

  echo "→ installing dependencies"
  python3 -m pip install -e "backend[dev]" >/dev/null
  (cd frontend && npm install --no-audit --no-fund >/dev/null)

  export MARKETRADAR_DATABASE_URL="${MARKETRADAR_DATABASE_URL:-postgresql+psycopg://marketradar:marketradar@localhost:5432/marketradar}"
  echo "→ migrating, seeding and running the pipeline"
  (cd backend && python3 -m alembic upgrade head \
    && python3 -m marketradar.cli seed \
    && python3 -m marketradar.cli pipeline \
    && python3 -m marketradar.cli research ai-memory-demand)

  echo "→ starting API on :8000 and web on :3000"
  (cd backend && python3 -m uvicorn marketradar.api.app:app --port 8000 &)
  (cd frontend && MARKETRADAR_API_URL=http://localhost:8000 npm run dev)
  exit 0
fi

# Two distinct failures that a single `docker info` check would conflate. Reporting
# "Docker is not running" when Docker Desktop is running and only the CLI is off PATH
# sends people to fix the wrong thing.
if ! command -v docker >/dev/null 2>&1; then
  # printf is a shell builtin: this message must survive the very PATH breakage it reports.
  printf '%s\n' >&2 \
    "The 'docker' command is not on your PATH." \
    "" \
    "  * If Docker Desktop is already installed and running, open a NEW terminal window" \
    "    and try again. Its installer updates your shell profile, and windows opened" \
    "    beforehand do not see that change." \
    "  * If it is not installed: https://www.docker.com/products/docker-desktop/" \
    "  * To run without Docker at all: ./scripts/start.sh --native"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  printf '%s\n' >&2 \
    "The 'docker' command works, but its engine is not reachable." \
    "" \
    "  * Open Docker Desktop and wait until it reports 'Engine running'." \
    "  * To run without Docker at all: ./scripts/start.sh --native"
  exit 1
fi

docker compose up --build -d
echo "→ waiting for the stack (migrations, seed, pipeline and research run first)"
for _ in $(seq 1 120); do
  if curl -fsS http://localhost:3000 >/dev/null 2>&1; then
    echo
    echo "  Market Radar is running:  http://localhost:3000"
    echo "  API and OpenAPI docs:     http://localhost:8000/docs"
    echo
    echo "  Stop with: docker compose down"
    exit 0
  fi
  sleep 2
done

echo "The stack did not become ready in time. Inspect it with: docker compose logs" >&2
exit 1
