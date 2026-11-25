# ==============================================================================
# Backend Dockerfile — FastAPI + SQLAlchemy 2.0 + Alembic (Python 3.12)
# Build context: ./backend
#
# Dependency Strategy:
#   Strictly requires hash-verified `requirements.lock` (generated via
#   `uv pip compile --universal --generate-hashes`) with `pip --require-hashes`.
#   Fails explicitly if `requirements.lock` is missing (no unhashed fallback).
# ==============================================================================

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements.lock* /build/

RUN python -m pip install --upgrade pip && \
    if [ ! -f requirements.lock ]; then \
        echo "ERROR: backend/requirements.lock is required for hash-verified dependency installs." >&2; \
        exit 1; \
    fi && \
    pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . /build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 10001 appgroup && \
    useradd --uid 10001 --gid appgroup --shell /usr/sbin/nologin --create-home appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appgroup . /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; resp = urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=3); sys.exit(0 if resp.status == 200 else 1)"]

# App-level H-04 trusted-proxy parsing (TRUSTED_PROXY_IPS) validates TCP peer IP
# before reading X-Forwarded-For. If overriding CMD behind a reverse proxy with
# `--proxy-headers`, set `--forwarded-allow-ips=<exact-proxy-ip-or-cidr>` (never `*`).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "15"]
