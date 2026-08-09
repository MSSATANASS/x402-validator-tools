"""OpenTelemetry instrumentation (opt-in).

Enable when either is set:
- ``OTEL_ENABLED=1``
- ``OTEL_EXPORTER_OTLP_ENDPOINT`` (standard OTEL env)

Requires optional extras::

    pip install "x402-validator-tools[otel]"

Without the packages installed, setup is a no-op so production stays lean.
"""

from __future__ import annotations

import os
from typing import Any

from api_server.logging_config import get_logger

log = get_logger()


def otel_enabled() -> bool:
    if os.environ.get("OTEL_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def setup_telemetry(app: Any) -> bool:
    """Instrument FastAPI ``app``. Returns True if OTEL was activated."""
    if not otel_enabled():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning(
            "otel_import_failed — install extras: pip install 'x402-validator-tools[otel]'",
            extra={"event": "otel.import_failed"},
        )
        return False

    service = os.environ.get("OTEL_SERVICE_NAME", "x402-validator-api")
    resource = Resource.create({"service.name": service})
    provider = TracerProvider(resource=resource)
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if endpoint:
        # Accept base URL or full traces path
        exporter = OTLPSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
    else:
        # Enabled but no exporter: still instrument for in-process providers
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        if os.environ.get("OTEL_CONSOLE_EXPORT", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,health,static")
    log.info(
        "otel_enabled",
        extra={"event": "otel.enabled", "service": service, "endpoint": endpoint or None},
    )
    return True


def start_audit_span(name: str = "x402.audit"):
    """Context manager for an audit span; no-op if OTEL inactive."""
    if not otel_enabled():
        from contextlib import nullcontext

        return nullcontext()
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("x402.api")
        return tracer.start_as_current_span(name)
    except Exception:
        from contextlib import nullcontext

        return nullcontext()
