#!/usr/bin/env bash
# Market Radar — bring the whole stack up on this machine.
#
#   ./scripts/start.sh            # containers (needs Docker)
#   ./scripts/start.sh --native   # host processes (needs Python 3.11+, Node 20+, PostgreSQL 16)
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--native" ]]; then
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

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop, or use: ./scripts/start.sh --native" >&2
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
