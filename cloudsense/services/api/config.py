"""Pydantic Settings — CloudSense Configuration."""
from __future__ import annotations
from functools import lru_cache
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    app_env: str = "development"
    debug: bool = False
    secret_key: SecretStr = Field(default=SecretStr("dev-secret"))
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "cloudsense"
    clickhouse_user: str = "default"
    clickhouse_password: SecretStr = Field(default=SecretStr(""))
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "cloudsense"
    postgres_user: str = "cloudsense"
    postgres_password: SecretStr = Field(default=SecretStr("cloudsense"))
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic_billing: str = "billing-events"
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    llm_default_model: str = "claude-3-5-sonnet-20241022"
    slack_bot_token: SecretStr | None = None
    slack_signing_secret: SecretStr | None = None
    slack_channel: str = "#finops-alerts"
    pagerduty_service_key: SecretStr | None = None
    opsgenie_api_key: SecretStr | None = None
    mlflow_tracking_uri: str = "http://localhost:5000"
    mlflow_experiment_name: str = "cloudsense-forecasting"
    opa_url: str = "http://localhost:8181/v1/data/cloudsense"

    # Phase 5.1 — Multi-tenant & SSO
    base_url: str = "http://localhost:8000"
    jwt_algorithm: str = "HS256"
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 2592000
    saml_sp_entity_id: str = "urn:cloudsense:sp"
    oidc_default_scopes: list[str] = ["openid", "email", "profile"]
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    aws_region: str = "us-east-1"
    rollback_window_days: int = 7
    auto_approve_non_production: bool = True
    @property
    def postgres_dsn(self) -> str:
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password.get_secret_value()}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

@lru_cache
def get_settings() -> Settings:
    return Settings()
