"""Distributed Tracing Configuration."""
from __future__ import annotations
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from cloudsense.services.api.config import Settings, get_settings

def setup_tracing(app=None, settings: Settings | None = None) -> trace.Tracer:
    settings = settings or get_settings()
    resource = Resource.create({SERVICE_NAME: "cloudsense-api", SERVICE_VERSION: "0.3.0", "deployment.environment": settings.app_env})
    provider = TracerProvider(resource=resource)
    try:
        otlp = OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
        provider.add_span_processor(BatchSpanProcessor(otlp))
    except Exception: pass
    if settings.debug: provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    if app: FastAPIInstrumentor.instrument_app(app)
    return trace.get_tracer("cloudsense")
