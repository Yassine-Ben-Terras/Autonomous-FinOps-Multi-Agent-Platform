"""
CloudSense — Application Configuration
Loaded from environment variables / .env file.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    clickhouse_host: str = Field(default="localhost", alias="CLICKHOUSE_HOST")
    clickhouse_port: int = Field(default=8123, alias="CLICKHOUSE_PORT")
    clickhouse_database: str = Field(default="cloudsense", alias="CLICKHOUSE_DATABASE")
    clickhouse_user: str = Field(default="default", alias="CLICKHOUSE_USER")
    clickhouse_password: SecretStr = Field(default=SecretStr(""), alias="CLICKHOUSE_PASSWORD")
    postgres_dsn: str = Field(
        default="postgresql://cloudsense:cloudsense@localhost:5432/cloudsense",
        alias="POSTGRES_DSN",
    )
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class KafkaSettings(BaseSettings):
    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    kafka_group_id: str = Field(default="cloudsense-agents", alias="KAFKA_GROUP_ID")
    kafka_billing_topic: str = Field(default="cloudsense.billing.events", alias="KAFKA_BILLING_TOPIC")
    kafka_anomaly_topic: str = Field(default="cloudsense.anomalies", alias="KAFKA_ANOMALY_TOPIC")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class RedisSettings(BaseSettings):
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_agent_memory_ttl: int = Field(default=3600, alias="REDIS_AGENT_MEMORY_TTL")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AWSSettings(BaseSettings):
    aws_access_key_id: SecretStr | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: SecretStr | None = Field(default=None, alias="AWS_SECRET_ACCESS_KEY")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    aws_role_arn: str | None = Field(default=None, alias="AWS_ROLE_ARN")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AzureSettings(BaseSettings):
    azure_tenant_id: SecretStr | None = Field(default=None, alias="AZURE_TENANT_ID")
    azure_client_id: SecretStr | None = Field(default=None, alias="AZURE_CLIENT_ID")
    azure_client_secret: SecretStr | None = Field(default=None, alias="AZURE_CLIENT_SECRET")
    azure_subscription_id: str | None = Field(default=None, alias="AZURE_SUBSCRIPTION_ID")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class GCPSettings(BaseSettings):
    gcp_project_id: str | None = Field(default=None, alias="GCP_PROJECT_ID")
    gcp_service_account_json: SecretStr | None = Field(default=None, alias="GCP_SERVICE_ACCOUNT_JSON")
    gcp_billing_dataset: str = Field(default="billing_export", alias="GCP_BILLING_DATASET")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class LLMSettings(BaseSettings):
    llm_provider: Literal["anthropic", "openai", "ollama"] = Field(default="anthropic", alias="LLM_PROVIDER")
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL")
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="llama3", alias="OLLAMA_MODEL")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class AppSettings(BaseSettings):
    app_name: str = "CloudSense"
    app_version: str = "0.1.0"
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO", alias="LOG_LEVEL")
    environment: Literal["development", "staging", "production"] = Field(default="development", alias="ENVIRONMENT")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")
    api_secret_key: SecretStr = Field(default=SecretStr("change-me-in-production"), alias="API_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(default=60, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    opa_url: str = Field(default="http://localhost:8181", alias="OPA_URL")
    langchain_tracing_v2: bool = Field(default=False, alias="LANGCHAIN_TRACING_V2")
    langchain_api_key: SecretStr | None = Field(default=None, alias="LANGCHAIN_API_KEY")
    auto_approve_non_production: bool = Field(default=True, alias="AUTO_APPROVE_NON_PRODUCTION")
    rollback_window_days: int = Field(default=7, alias="ROLLBACK_WINDOW_DAYS")

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        return v.lower()

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class Settings(BaseSettings):
    """Aggregate settings object — import this everywhere."""
    app: AppSettings = Field(default_factory=AppSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    aws: AWSSettings = Field(default_factory=AWSSettings)
    azure: AzureSettings = Field(default_factory=AzureSettings)
    gcp: GCPSettings = Field(default_factory=GCPSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance. Call get_settings.cache_clear() in tests."""
    return Settings()
