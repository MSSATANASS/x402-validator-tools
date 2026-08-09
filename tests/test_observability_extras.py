"""Prometheus, key rate limits, audit cache, OTEL setup no-op paths."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from api_server import audit_cache, ratelimit
from api_server.audit_cache import (
    AuditResponseCache,
    make_cache_key,
    should_skip_cache_request,
    should_skip_cache_store,
)
from api_server.telemetry import otel_enabled, setup_telemetry


@pytest.fixture(autouse=True)
def _clean_limiters_and_cache():
    ratelimit.reset_limiter()
    audit_cache.reset_audit_cache()
    yield
    ratelimit.reset_limiter()
    audit_cache.reset_audit_cache()


class TestKeyRateLimit:
    def test_allow_until_limit(self, monkeypatch):
        monkeypatch.setenv("API_KEY_RATE_LIMIT_ENABLED", "1")
        monkeypatch.setenv("RATE_LIMIT_KEY_FREE", "3")
        # Rebuild window is fixed at import; limits are read per call.
        ratelimit.reset_limiter()
        key = "test-key-aaaaaaaaaaaaaaaa"
        assert ratelimit.allow_api_key(key, "free") is True
        assert ratelimit.allow_api_key(key, "free") is True
        assert ratelimit.allow_api_key(key, "free") is True
        assert ratelimit.allow_api_key(key, "free") is False

    def test_disabled(self, monkeypatch):
        monkeypatch.setenv("API_KEY_RATE_LIMIT_ENABLED", "0")
        key = "k" * 32
        for _ in range(50):
            assert ratelimit.allow_api_key(key, "free") is True


class TestAuditCache:
    def test_ttl_hit_miss(self, monkeypatch):
        monkeypatch.setenv("AUDIT_CACHE_TTL_SECONDS", "60")
        cache = AuditResponseCache(time_func=lambda: 1000.0)
        key = make_cache_key("https://ex.com", "standard")
        assert cache.get(key) is None
        cache.set(key, {"url": "https://ex.com", "overall": "PASS"})
        hit = cache.get(key)
        assert hit is not None and hit["overall"] == "PASS"

    def test_skip_ai_and_batch_applicable(self):
        assert should_skip_cache_request(advise=True, explain=False) is True
        assert should_skip_cache_request(advise=False, explain=False) is False
        assert should_skip_cache_store(
            [{"name": "batch_settlement_requirements", "details": {"applicable": True}}]
        )
        assert not should_skip_cache_store(
            [{"name": "batch_settlement_requirements", "details": {"applicable": False}}]
        )


class TestMetricsEndpoint:
    def test_metrics_exposes_prometheus(self, tmp_path, monkeypatch):
        monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "k.json"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("METRICS_ENABLED", "1")
        app_mod = importlib.import_module("api_server.app")
        app_mod = importlib.reload(app_mod)
        client = TestClient(app_mod.app)
        client.get("/health")
        r = client.get("/metrics")
        assert r.status_code == 200
        assert "x402_http_requests_total" in r.text or "http" in r.text.lower()

    def test_metrics_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "k.json"))
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("METRICS_ENABLED", "0")
        app_mod = importlib.import_module("api_server.app")
        app_mod = importlib.reload(app_mod)
        client = TestClient(app_mod.app)
        r = client.get("/metrics")
        assert r.status_code == 404


class TestOtel:
    def test_setup_noop_when_disabled(self, monkeypatch):
        monkeypatch.delenv("OTEL_ENABLED", raising=False)
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        assert otel_enabled() is False
        from fastapi import FastAPI

        assert setup_telemetry(FastAPI()) is False
