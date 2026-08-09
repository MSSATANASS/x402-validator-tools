"""Tests for structured JSON logging and request-id middleware."""

from __future__ import annotations

import importlib
import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_server.logging_config import (
    JsonFormatter,
    RequestContextMiddleware,
    bind_request_id,
    get_logger,
    get_request_id,
    setup_logging,
)


class TestJsonFormatter:
    def test_emits_json_with_extra_fields(self):
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="x402.api",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="audit_completed",
            args=(),
            exc_info=None,
        )
        record.event = "audit.completed"  # type: ignore[attr-defined]
        record.overall = "PASS"  # type: ignore[attr-defined]
        record.latency_ms = 12.5  # type: ignore[attr-defined]
        token = bind_request_id("abc123")
        try:
            line = fmt.format(record)
        finally:
            from api_server.logging_config import _request_id_var

            _request_id_var.reset(token)
        data = json.loads(line)
        assert data["msg"] == "audit_completed"
        assert data["event"] == "audit.completed"
        assert data["overall"] == "PASS"
        assert data["latency_ms"] == 12.5
        assert data["request_id"] == "abc123"
        assert data["level"] == "INFO"


class TestSetupLogging:
    def test_json_when_log_format_json(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "json")
        # Force reconfigure
        log = get_logger()
        log.handlers.clear()
        log._x402_configured = False  # type: ignore[attr-defined]
        setup_logging("DEBUG")
        assert any(isinstance(h.formatter, JsonFormatter) for h in log.handlers)

    def test_text_when_log_format_text(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "text")
        log = get_logger()
        log.handlers.clear()
        log._x402_configured = False  # type: ignore[attr-defined]
        setup_logging("INFO")
        assert log.handlers
        assert not isinstance(log.handlers[0].formatter, JsonFormatter)


class TestRequestMiddleware:
    def test_sets_request_id_header(self):
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware)

        @app.get("/ping")
        def ping():
            return {"rid": get_request_id()}

        client = TestClient(app)
        r = client.get("/ping")
        assert r.status_code == 200
        assert "x-request-id" in {k.lower(): v for k, v in r.headers.items()}
        rid = r.headers.get("x-request-id") or r.headers.get("X-Request-Id")
        assert rid
        assert r.json()["rid"] == rid

    def test_propagates_incoming_request_id(self):
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware)

        @app.get("/ping")
        def ping():
            return {"rid": get_request_id()}

        client = TestClient(app)
        r = client.get("/ping", headers={"X-Request-Id": "client-trace-99"})
        assert r.headers.get("x-request-id") == "client-trace-99"
        assert r.json()["rid"] == "client-trace-99"


class TestAppWiresMiddleware:
    def test_health_returns_request_id(self, tmp_path, monkeypatch):
        monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "k.json"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("LOG_FORMAT", "json")
        app_mod = importlib.import_module("api_server.app")
        app_mod = importlib.reload(app_mod)
        client = TestClient(app_mod.app)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.headers.get("x-request-id") or r.headers.get("X-Request-Id")
