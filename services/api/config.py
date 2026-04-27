"""CloudSense API — Application settings (pydantic-settings v2)."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── General ────────────────────────────────────────────────
    env: str             = Field(default="development")
    secret_key: str      = Field(default="change-me-in-production-min-32-chars")
    cors_origins: list[str] = Field(default=["http://localhost:3000", "http://localhost:5173"])

    # ── ClickHouse ─────────────────────────────────────────────
    clickhouse_host:     str = Field(default="localhost")
    clickhouse_port:     int = Field(default=8123)
    clickhouse_db:       str = Field(default="focus")
    clickhouse_user:     str = Field(default="cloudsense")
    clickhouse_password: str = Field(default="dev_password_change_me")

    # ── PostgreSQL ─────────────────────────────────────────────
    postgres_dsn: str = Field(
        default="postgresql+asyncpg://cloudsense:dev_password_change_me@localhost:5432/cloudsense"
    )

    # ── Redis ──────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ── Kafka (KRaft — no ZooKeeper) ───────────────────────────
    kafka_bootstrap_servers: str = Field(default="localhost:9092")

    # ── Ingestion ──────────────────────────────────────────────
    ingestion_batch_size:   int = Field(default=5_000)
    ingestion_max_retries:  int = Field(default=3)


settings = Settings()
