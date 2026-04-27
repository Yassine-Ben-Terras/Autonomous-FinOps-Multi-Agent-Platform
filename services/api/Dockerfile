# ============================================================
# CloudSense API — Dockerfile (multi-stage)
# ============================================================
# Stage 1: builder  — installs Python deps
# Stage 2: production — minimal runtime image
# Stage 3: development — adds hot-reload tools
# ============================================================

# ── Stage 1: builder ─────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir poetry==1.8.3 && \
    poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --only main --no-interaction --no-ansi

# ── Stage 2: production ──────────────────────────────────────
FROM python:3.12-slim AS production

# Non-root user for security
RUN groupadd -r cloudsense && useradd -r -g cloudsense cloudsense

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY --chown=cloudsense:cloudsense . .

USER cloudsense

# FastAPI listens on 8000
EXPOSE 8000

# Health check — used by Docker Compose and Kubernetes liveness probe
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "services.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--access-log", \
     "--log-level", "info"]

# ── Stage 3: development ─────────────────────────────────────
FROM production AS development

USER root
RUN pip install --no-cache-dir watchfiles pytest pytest-asyncio pytest-cov httpx moto

USER cloudsense

# Hot-reload via watchfiles
CMD ["uvicorn", "services.api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--reload", \
     "--reload-dir", "services", \
     "--reload-dir", "cloudsense"]
