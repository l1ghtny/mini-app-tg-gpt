# ---- Base (deps & build tools) ----
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# system libs you likely need:
# - libheif1 for HEIC -> PNG/JPEG via pillow-heif
# - curl for healthchecks / debugging
RUN apt-get update && apt-get install -y --no-install-recommends \
    libheif1 ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Poetry globally, but install packages into the system site-packages
RUN pip install --no-cache-dir "poetry==1.8.3" && \
    poetry config virtualenvs.create false

# ---- Deps layer (leverages Docker cache) ----
FROM base AS deps
COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-interaction --no-ansi

# ---- Runtime (copy code only after deps) ----
FROM deps AS runtime
# non-root user
RUN useradd -u 10001 -m appuser
COPY . .
# entrypoint needs exec perms
RUN chmod +x docker/entrypoint.sh || true
USER appuser

# sensible defaults; override via envs
ENV PORT=8000 \
    APP_MODULE="app.main:app" \
    UVICORN_WORKERS=1 \
    UVICORN_LOG_LEVEL=info

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

#ENTRYPOINT ["docker/entrypoint.sh"] # for alembic - needs the sh file to be used
CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--log-level", "info"]

## ---- Test target (optional) ----
#FROM base AS test
#COPY pyproject.toml poetry.lock* ./
#RUN poetry install --with dev --no-interaction --no-ansi
#COPY . .
## run tests (example; override in CI)
#CMD ["pytest", "-q"]

