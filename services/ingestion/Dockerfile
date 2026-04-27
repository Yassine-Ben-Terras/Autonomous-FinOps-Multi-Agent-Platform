# ============================================================
# CloudSense Ingestion Worker — Dockerfile (multi-stage)
# Runs Celery workers for billing ingestion tasks
# ============================================================

FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir poetry==1.8.3 && \
    poetry config virtualenvs.create false
COPY pyproject.toml poetry.lock* ./
RUN poetry install --no-root --only main --no-interaction --no-ansi

# ── Production ────────────────────────────────────────────────
FROM python:3.12-slim AS production

RUN groupadd -r cloudsense && useradd -r -g cloudsense cloudsense
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=cloudsense:cloudsense . .

USER cloudsense

# Celery worker — billing_ingestion queue
CMD ["celery", "-A", "services.ingestion.tasks", "worker", \
     "--loglevel=info", \
     "--concurrency=4", \
     "-Q", "billing_ingestion", \
     "--hostname=ingestion@%h"]

# ── Development ───────────────────────────────────────────────
FROM production AS development
USER root
RUN pip install --no-cache-dir watchdog
USER cloudsense

CMD ["celery", "-A", "services.ingestion.tasks", "worker", \
     "--loglevel=debug", \
     "--concurrency=2", \
     "-Q", "billing_ingestion", \
     "--hostname=ingestion-dev@%h"]
