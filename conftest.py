"""Root conftest — mock heavy packages before any test import."""
import sys
from unittest.mock import MagicMock

HEAVY_MOCKS = [
    "boto3", "botocore", "botocore.exceptions",
    "langchain", "langchain.tools", "langchain.agents", "langchain.schema",
    "langchain.hub", "langchain_anthropic", "langchain_openai", "langsmith",
    "langgraph", "langgraph.graph", "langgraph.graph.state",
    "prophet", "mlflow",
    "xgboost", "sklearn", "sklearn.metrics", "sklearn.ensemble",
    "opentelemetry", "opentelemetry.sdk", "opentelemetry.sdk.resources",
    "opentelemetry.sdk.trace", "opentelemetry.sdk.trace.export",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.instrumentation.fastapi", "opentelemetry.trace",
    "clickhouse_driver", "clickhouse_driver.asyncio",
    "redis", "celery", "kafka", "kafka_python",
    "asyncpg",
    "sqlalchemy", "sqlalchemy.ext.asyncio", "sqlalchemy.orm",
    "alembic",
    "numpy", "pandas", "scipy", "joblib",
    "azure", "azure.identity", "azure.mgmt",
    "azure.mgmt.costmanagement", "azure.mgmt.costmanagement.models",
    "google", "google.cloud", "google.cloud.bigquery", "google.cloud.billing",
]

for _mod in HEAVY_MOCKS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()
