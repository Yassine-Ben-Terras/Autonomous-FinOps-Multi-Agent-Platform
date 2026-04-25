# CloudSense API — Production Dockerfile
# Multi-stage build for minimal image size.

FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="CloudSense API"
LABEL org.opencontainers.image.description="FinOps Multi-Agent Platform API"
LABEL org.opencontainers.image.version="0.1.0"

# Non-root user for security
RUN useradd --create-home --shell /bin/bash cloudsense
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/cloudsense/.local
ENV PATH=/home/cloudsense/.local/bin:$PATH

# Copy application source
COPY cloudsense/ ./cloudsense/
COPY pyproject.toml .

USER cloudsense

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD wget --spider -q http://localhost:8000/health || exit 1

CMD ["uvicorn", "cloudsense.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
