# ═══════════════════════════════════════════════════════════════════════════════
# CloudSense API — Production Docker Image
# Multi-stage build for optimized image size
# ═══════════════════════════════════════════════════════════════════════════════

# ── Builder stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Security: run as non-root
RUN groupadd -r cloudsense && useradd -r -g cloudsense cloudsense

# Runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /home/cloudsense/.local
ENV PATH=/home/cloudsense/.local/bin:$PATH

# Copy application code
COPY --chown=cloudsense:cloudsense sdk/ ./sdk/
COPY --chown=cloudsense:cloudsense connectors/ ./connectors/
COPY --chown=cloudsense:cloudsense agents/ ./agents/
COPY --chown=cloudsense:cloudsense services/ ./services/
COPY --chown=cloudsense:cloudsense recommendations/ ./recommendations/
COPY --chown=cloudsense:cloudsense policy/ ./policy/
COPY --chown=cloudsense:cloudsense observability/ ./observability/
COPY --chown=cloudsense:cloudsense bot/ ./bot/

# Copy config files
COPY --chown=cloudsense:cloudsense requirements.txt .
COPY --chown=cloudsense:cloudsense .env.example .

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

USER cloudsense

EXPOSE 8000

CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
