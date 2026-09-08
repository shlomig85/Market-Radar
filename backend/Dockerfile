# Market Radar backend: API, pipeline and CLI.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so a source change does not re-resolve the dependency tree.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0.30" \
      "psycopg[binary]>=3.1" "alembic>=1.13" "pydantic>=2.7" "pydantic-settings>=2.3" \
      "structlog>=24.1" "typer>=0.12" "httpx>=0.27" "python-dateutil>=2.9"

COPY alembic.ini ./
COPY alembic ./alembic
COPY marketradar ./marketradar
RUN pip install --no-cache-dir --no-deps -e .

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 marketradar && chown -R marketradar:marketradar /app
USER marketradar

EXPOSE 8000
CMD ["uvicorn", "marketradar.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
