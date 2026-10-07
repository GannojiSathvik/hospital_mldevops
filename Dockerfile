# syntax=docker/dockerfile:1
# =============================================================================
# Healthcare Readmission API — production image
# -----------------------------------------------------------------------------
# Multi-stage build:
#   builder : has pip + build cache, installs requirements-api.txt into a venv
#   runtime : copies ONLY the venv + app code, runs as non-root appuser
# Why: the final image has no compilers/pip caches, fewer packages => fewer CVEs
# and a smaller attack surface. No secrets are baked in — API_KEYS etc. are
# supplied at runtime via environment variables / orchestrator secrets.
# For reproducibility pin the base by digest, e.g.
#   FROM python:3.11-slim@sha256:<digest>   (Dependabot `docker` updates it)
# =============================================================================

# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="healthcare-readmission-api" \
      org.opencontainers.image.description="30-day readmission risk API (educational demo, not for clinical use)" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENV=production

# libgomp1: OpenMP runtime needed by XGBoost. Clean apt lists in the same layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Dedicated unprivileged user (fixed uid so K8s runAsUser / volumes match).
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Copy ONLY what the API needs at runtime (see .dockerignore as a second guard).
COPY --chown=root:root api/ ./api/
COPY --chown=root:root src/ ./src/
COPY --chown=root:root configs/ ./configs/
COPY --chown=root:root ui/ ./ui/
COPY --chown=root:root models/ ./models/
# Drift reference for POST /monitor/drift (synthetic training split, no PII).
COPY --chown=root:root data/processed/train.csv ./data/processed/train.csv

# Code is root-owned and read-only for appuser; only these dirs are writable
# (mount them as volumes/tmpfs when the root filesystem is read-only).
RUN mkdir -p /app/logs /app/data/inference /app/reports/drift \
    && chown -R appuser:appuser /app/logs /app/data /app/reports

USER appuser

EXPOSE 8000

# Python stdlib probe — no curl/wget needed in the image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--proxy-headers"]
