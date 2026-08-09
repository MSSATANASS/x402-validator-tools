"""Prometheus metrics for the API server.

Exposes ``GET /metrics`` (Prometheus text format). Disable with
``METRICS_ENABLED=0``.

Metrics:
- ``x402_http_requests_total`` — method, path_template, status_code
- ``x402_http_request_duration_seconds`` — latency histogram
- ``x402_audits_total`` — source, overall, mode
- ``x402_audit_duration_seconds`` — audit pipeline latency
- ``x402_rate_limit_rejections_total`` — kind (ip|api_key)
- ``x402_audit_cache_events_total`` — event (hit|miss|store|skip)
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )
except ImportError:  # pragma: no cover
    CONTENT_TYPE_LATEST = "text/plain"
    Counter = Histogram = generate_latest = None  # type: ignore[misc, assignment]


def metrics_enabled() -> bool:
    return os.environ.get("METRICS_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


if Counter is not None:
    HTTP_REQUESTS = Counter(
        "x402_http_requests_total",
        "HTTP requests",
        ["method", "path", "status_code"],
    )
    HTTP_LATENCY = Histogram(
        "x402_http_request_duration_seconds",
        "HTTP request latency",
        ["method", "path"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    )
    AUDITS = Counter(
        "x402_audits_total",
        "Completed audits",
        ["source", "overall", "mode"],
    )
    AUDIT_LATENCY = Histogram(
        "x402_audit_duration_seconds",
        "End-to-end audit pipeline latency",
        ["source", "mode"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
    )
    RATE_LIMIT = Counter(
        "x402_rate_limit_rejections_total",
        "Rate limit rejections",
        ["kind"],
    )
    CACHE_EVENTS = Counter(
        "x402_audit_cache_events_total",
        "Audit response cache events",
        ["event"],
    )
else:  # pragma: no cover
    HTTP_REQUESTS = HTTP_LATENCY = AUDITS = AUDIT_LATENCY = RATE_LIMIT = CACHE_EVENTS = None


def _path_label(path: str) -> str:
    """Collapse high-cardinality path segments for metrics."""
    if path.startswith("/static/"):
        return "/static/*"
    if path.startswith("/admin/keys/") and path != "/admin/keys":
        return "/admin/keys/{key}"
    # Keep route templates short
    if len(path) > 64:
        return path[:64]
    return path or "/"


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not metrics_enabled() or HTTP_REQUESTS is None:
            return await call_next(request)
        if request.url.path == "/metrics":
            return await call_next(request)
        started = time.perf_counter()
        status = "500"
        path = _path_label(request.url.path)
        method = request.method
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            elapsed = time.perf_counter() - started
            try:
                HTTP_REQUESTS.labels(method=method, path=path, status_code=status).inc()
                HTTP_LATENCY.labels(method=method, path=path).observe(elapsed)
            except Exception:
                pass


def record_audit_metrics(
    *,
    source: str,
    overall: str,
    mode: str,
    latency_seconds: float,
) -> None:
    if not metrics_enabled() or AUDITS is None:
        return
    try:
        AUDITS.labels(source=source, overall=overall or "UNKNOWN", mode=mode).inc()
        AUDIT_LATENCY.labels(source=source, mode=mode).observe(latency_seconds)
    except Exception:
        pass


def record_rate_limit(kind: str) -> None:
    if not metrics_enabled() or RATE_LIMIT is None:
        return
    try:
        RATE_LIMIT.labels(kind=kind).inc()
    except Exception:
        pass


def record_cache(event: str) -> None:
    if not metrics_enabled() or CACHE_EVENTS is None:
        return
    try:
        CACHE_EVENTS.labels(event=event).inc()
    except Exception:
        pass


def metrics_response() -> Response:
    if generate_latest is None:
        return Response("# prometheus_client not installed\n", media_type="text/plain")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
