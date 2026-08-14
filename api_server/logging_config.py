"""Structured JSON logging for the API server.

Emits one JSON object per line (JSONL) so Fly.io / Docker log drains can
index fields without regex. Toggle with ``LOG_FORMAT=json|text`` (default
``json`` when a Fly.io or production environment looks active, else ``text``).

Request correlation: middleware sets ``request.state.request_id`` and
binds it into the logging context for the duration of the request.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_logger_name = "x402.api"


def get_request_id() -> str | None:
    return _request_id_var.get()


def bind_request_id(request_id: str | None):
    """Return a context-var token; reset with ``_request_id_var.reset(token)``."""
    return _request_id_var.set(request_id)


class JsonFormatter(logging.Formatter):
    """Minimal JSON log formatter (stdlib only — no structlog dependency)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = get_request_id()
        if rid:
            payload["request_id"] = rid
        # Extra structured fields (logger.info("…", extra={...}))
        for key in (
            "event",
            "method",
            "path",
            "status_code",
            "latency_ms",
            "url",
            "mode",
            "overall",
            "plan_id",
            "source",
            "client_ip",
            "error_type",
            "checks_total",
            "checks_failed",
        ):
            val = getattr(record, key, None)
            if val is not None:
                payload[key] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _use_json_format() -> bool:
    explicit = os.environ.get("LOG_FORMAT", "").strip().lower()
    if explicit in ("json", "text"):
        return explicit == "json"
    # Default JSON on Fly.io / production-like hosts.
    env = (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").lower()
    return bool(os.environ.get("FLY_APP_NAME") or env in ("prod", "production"))


def setup_logging(level: str | None = None) -> logging.Logger:
    """Configure root handler once; return the API logger."""
    log_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logger = logging.getLogger(_logger_name)
    if getattr(logger, "_x402_configured", False):
        logger.setLevel(log_level)
        return logger

    handler = logging.StreamHandler(sys.stdout)
    if _use_json_format():
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s"
            )
        )
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(log_level)
    logger.propagate = False
    logger._x402_configured = True  # type: ignore[attr-defined]
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_logger_name)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach ``X-Request-Id`` and log one access line per request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id") or request.headers.get(
            "x-correlation-id"
        )
        request_id = (incoming or "").strip() or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-Id"] = request_id
            return response
        except Exception:
            get_logger().exception(
                "unhandled_error",
                extra={
                    "event": "http.unhandled",
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            # Skip noisy static assets at DEBUG only
            path = request.url.path
            log = get_logger()
            extra = {
                "event": "http.access",
                "method": request.method,
                "path": path,
                "status_code": status_code,
                "latency_ms": latency_ms,
                "client_ip": request.client.host if request.client else None,
            }
            if path.startswith("/static/") or path in ("/health", "/favicon.ico"):
                log.debug("access", extra=extra)
            else:
                log.info("access", extra=extra)
            _request_id_var.reset(token)
