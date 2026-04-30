"""
CloudSense Observability — Distributed Tracing & Metrics

Provides OpenTelemetry instrumentation for:
- Agent reasoning steps
- Tool execution
- API requests
- Connector calls
- Policy evaluations

Plus LangSmith integration for LLM observability.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

from services.api.config import get_settings

logger = logging.getLogger(__name__)


class CloudSenseTracer:
    """OpenTelemetry tracer wrapper for CloudSense.

    Falls back to logging if OTel is not configured.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self._tracer: Any = None
        self._meter: Any = None
        self._langsmith_enabled = self.settings.enable_langsmith_tracing

    def _get_tracer(self) -> Any:
        """Lazy-init OpenTelemetry tracer."""
        if self._tracer is None:
            try:
                from opentelemetry import trace
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                from opentelemetry.sdk.resources import Resource, SERVICE_NAME
                from opentelemetry.sdk.trace import TracerProvider
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                resource = Resource.create({SERVICE_NAME: "cloudsense"})
                provider = TracerProvider(resource=resource)

                if self.settings.otel_exporter_otlp_endpoint:
                    exporter = OTLPSpanExporter(
                        endpoint=self.settings.otel_exporter_otlp_endpoint,
                    )
                    provider.add_span_processor(BatchSpanProcessor(exporter))

                trace.set_tracer_provider(provider)
                self._tracer = trace.get_tracer("cloudsense")
                logger.info("OpenTelemetry tracer initialized")

            except ImportError:
                logger.warning("opentelemetry not installed, using logging fallback")
                self._tracer = _LoggingTracer()

        return self._tracer

    @contextmanager
    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        """Start a traced span."""
        tracer = self._get_tracer()
        try:
            with tracer.start_as_current_span(name) as span:
                if attributes:
                    for key, value in attributes.items():
                        span.set_attribute(key, value)
                yield span
        except Exception:
            yield None

    def trace_agent_reasoning(
        self,
        agent_name: str,
        task_id: str,
        reasoning: str,
    ) -> None:
        """Trace an agent's reasoning step."""
        with self.start_span(
            f"agent.reasoning.{agent_name}",
            {"agent": agent_name, "task_id": task_id},
        ) as span:
            if span:
                span.set_attribute("reasoning", reasoning[:1000])
            logger.debug("[%s] Reasoning: %s", agent_name, reasoning[:500])

    def trace_tool_call(
        self,
        tool_name: str,
        agent_name: str,
        input_params: dict[str, Any],
        output: Any,
        duration_ms: float,
    ) -> None:
        """Trace a tool execution."""
        with self.start_span(
            f"tool.{tool_name}",
            {
                "agent": agent_name,
                "tool": tool_name,
                "duration_ms": duration_ms,
            },
        ) as span:
            if span:
                span.set_attribute("input_keys", list(input_params.keys()))
                span.set_attribute("output_type", type(output).__name__)
            logger.debug(
                "[%s] Tool %s executed in %.2fms",
                agent_name,
                tool_name,
                duration_ms,
            )

    def trace_recommendation(
        self,
        recommendation_id: str,
        category: str,
        savings: float,
        risk_level: str,
    ) -> None:
        """Trace a generated recommendation."""
        with self.start_span(
            "recommendation.generated",
            {
                "recommendation_id": recommendation_id,
                "category": category,
                "projected_savings": savings,
                "risk_level": risk_level,
            },
        ):
            logger.info(
                "Recommendation %s: %s ($%.2f, %s risk)",
                recommendation_id[:8],
                category,
                savings,
                risk_level,
            )

    def trace_policy_decision(
        self,
        recommendation_id: str,
        allowed: bool,
        policy_name: str,
        reason: str,
    ) -> None:
        """Trace a policy evaluation."""
        with self.start_span(
            "policy.evaluated",
            {
                "recommendation_id": recommendation_id,
                "allowed": allowed,
                "policy": policy_name,
            },
        ):
            logger.info(
                "Policy %s: recommendation %s %s — %s",
                policy_name,
                recommendation_id[:8],
                "ALLOWED" if allowed else "DENIED",
                reason,
            )

    def trace_connector_call(
        self,
        provider: str,
        operation: str,
        duration_ms: float,
        success: bool,
    ) -> None:
        """Trace a cloud connector API call."""
        with self.start_span(
            f"connector.{provider}.{operation}",
            {
                "provider": provider,
                "operation": operation,
                "duration_ms": duration_ms,
                "success": success,
            },
        ):
            logger.debug(
                "Connector %s/%s: %.2fms (%s)",
                provider,
                operation,
                duration_ms,
                "ok" if success else "failed",
            )

    # ── LangSmith Integration ───────────────────────────────────────────────

    def langsmith_trace(
        self,
        run_name: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Send a trace to LangSmith for LLM observability."""
        if not self._langsmith_enabled:
            return

        try:
            from langsmith import Client as LangSmithClient

            client = LangSmithClient(api_key=self.settings.langsmith_api_key)
            client.create_run(
                name=run_name,
                run_type="chain",
                inputs=inputs,
                outputs=outputs or {},
                project_name=self.settings.langsmith_project,
                tags=tags or [],
            )
        except ImportError:
            logger.debug("langsmith not installed, skipping trace")
        except Exception as exc:
            logger.warning("LangSmith trace failed: %s", exc)

    def trace_llm_call(
        self,
        model: str,
        prompt: str,
        response: str,
        tokens_used: int = 0,
    ) -> None:
        """Trace an LLM API call to LangSmith."""
        self.langsmith_trace(
            run_name=f"llm.{model}",
            inputs={"prompt": prompt[:2000]},
            outputs={"response": response[:2000]},
            tags=["llm", model],
        )
        logger.debug("LLM call (%s): %d tokens", model, tokens_used)


class _LoggingTracer:
    """Fallback tracer that just logs when OTel is unavailable."""

    @contextmanager
    def start_as_current_span(self, name: str):
        logger.debug("[TRACE] %s", name)
        yield _LoggingSpan()


class _LoggingSpan:
    """Fallback span for logging tracer."""

    def set_attribute(self, key: str, value: Any) -> None:
        logger.debug("[TRACE ATTR] %s = %s", key, value)


# Singleton
tracers = CloudSenseTracer()
