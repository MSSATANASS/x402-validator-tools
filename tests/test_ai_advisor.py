"""Tests for the Inception Labs-backed AI advisor and /validate wiring."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from api_server import ai_advisor


class _Check:
    """Mimics report checks consumed by the advisor."""

    def __init__(self, name: str, status: str, message: str):
        self.name = name
        self.check_name = name
        self.status = status
        self.message = message
        self.details = None


_CHECKS = [
    _Check("manifest_discovery", "PASS", "ok"),
    _Check("caip2_compliance", "FAIL", "Payment-Required header missing"),
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    import importlib
    import sys

    monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    importlib.import_module("api_server.keystore")
    importlib.import_module("api_server.app")

    keystore_mod = sys.modules["api_server.keystore"]
    app_mod = sys.modules["api_server.app"]
    importlib.reload(keystore_mod)
    importlib.reload(app_mod)
    return TestClient(app_mod.app), app_mod


def _fake_report():
    class _Report:
        target_url = "https://example.com"
        overall_status = "FAIL"
        summary = "1/2 checks passed"
        timestamp = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
        def __init__(self):
            self.checks = [
                _Check("manifest_discovery", "PASS", "ok"),
                _Check("caip2_compliance", "FAIL", "Payment-Required header missing"),
            ]

    return _Report()


def _mock_post(content: str = "Fix the CAIP-2 header."):
    captured = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = str(url)
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
            request=httpx.Request("POST", str(url)),
        )

    return fake_post, captured


_PROBE = {
    "check_name": "directory_cold_probe",
    "status": "PASS",
    "message": "ok",
    "details": {"method": "POST", "status_code": 402},
}

_BATCH = {
    "check_name": "batch_settlement_requirements",
    "status": "PASS",
    "message": "N/A — no batch-settlement offers",
    "details": {"applicable": False, "payload_source": "cold_probe_post", "status_code": 402},
}


async def _fake_audit(url, mode, timeout=10.0):
    return _fake_report(), _PROBE, _BATCH


class TestAdvisorUnit:
    def test_disabled_without_inception_key(self, monkeypatch):
        monkeypatch.delenv("INCEPTION_API_KEY", raising=False)
        assert ai_advisor.enabled() is False

    def test_returns_none_when_disabled(self, monkeypatch):
        import asyncio

        monkeypatch.delenv("INCEPTION_API_KEY", raising=False)
        assert asyncio.run(ai_advisor.advise("u", "FAIL", "s", _CHECKS)) is None

    def test_success_uses_inception_chat_contract(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("INCEPTION_API_KEY", "test-key")
        monkeypatch.setenv("INCEPTION_BASE_URL", "https://stub.example/v1/")
        fake_post, captured = _mock_post()
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        out = asyncio.run(
            ai_advisor.advise("https://example.com", "FAIL", "1/2", _CHECKS)
        )

        assert out == "Fix the CAIP-2 header."
        assert captured["url"] == "https://stub.example/v1/chat/completions"
        assert captured["headers"] == {
            "Authorization": "Bearer test-key",
            "Content-Type": "application/json",
        }
        assert captured["json"]["model"] == "mercury-2"
        assert captured["json"]["reasoning_effort"] == "low"
        assert captured["json"]["temperature"] == 0.5
        assert captured["json"]["messages"][0] == {"role": "system", "content": ai_advisor._SYSTEM}
        user_message = captured["json"]["messages"][1]["content"]
        assert "caip2_compliance" in user_message
        assert "manifest_discovery" not in user_message

    def test_graceful_on_transport_error(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("INCEPTION_API_KEY", "test-key")

        async def boom(self, url, **kwargs):
            raise httpx.ConnectError("nope")

        monkeypatch.setattr(httpx.AsyncClient, "post", boom)
        assert asyncio.run(ai_advisor.advise("u", "FAIL", "s", _CHECKS)) is None

    def test_graceful_on_bad_payload(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("INCEPTION_API_KEY", "test-key")

        async def bad(self, url, **kwargs):
            return httpx.Response(
                200, json={"unexpected": True}, request=httpx.Request("POST", str(url))
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", bad)
        assert asyncio.run(ai_advisor.advise("u", "FAIL", "s", _CHECKS)) is None


class TestSummarizeUnit:
    def test_summarize_sends_all_checks(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("INCEPTION_API_KEY", "test-key")
        fake_post, captured = _mock_post("Your site mostly works.")
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

        out = asyncio.run(
            ai_advisor.summarize("https://example.com", "FAIL", "1/2", _CHECKS)
        )

        assert out == "Your site mostly works."
        user_message = captured["json"]["messages"][1]["content"]
        assert "caip2_compliance" in user_message
        assert "manifest_discovery" in user_message
        assert captured["json"]["messages"][0]["content"] == ai_advisor._SUMMARY_SYSTEM
        assert captured["json"]["messages"][0]["content"] != ai_advisor._SYSTEM


class TestValidateWiring:
    def test_advise_attaches_advice(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.setenv("INCEPTION_API_KEY", "test-key")
        fake_post, captured = _mock_post("Rotate your manifest.")
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            response = tc.post(
                "/validate",
                json={"url": "https://example.com", "advise": True},
                headers={"X-API-Key": key},
            )
        assert response.status_code == 200
        assert response.json()["ai_advice"] == "Rotate your manifest."
        assert "caip2_compliance" in captured["json"]["messages"][1]["content"]

    def test_explain_attaches_summary(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.setenv("INCEPTION_API_KEY", "test-key")
        fake_post, captured = _mock_post("In short: mostly works.")
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            response = tc.post(
                "/validate",
                json={"url": "https://example.com", "explain": True},
                headers={"X-API-Key": key},
            )
        assert response.status_code == 200
        assert response.json()["ai_summary"] == "In short: mostly works."
        assert response.json()["ai_advice"] is None
        assert "manifest_discovery" in captured["json"]["messages"][1]["content"]

    def test_advise_and_explain_both_fire(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.setenv("INCEPTION_API_KEY", "test-key")
        payloads = []

        async def fake_post(self, url, **kwargs):
            payload = kwargs["json"]
            payloads.append(payload)
            content = "ADVICE-TEXT" if payload["messages"][0]["content"] == ai_advisor._SYSTEM else "SUMMARY-TEXT"
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": content}}]},
                request=httpx.Request("POST", str(url)),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            response = tc.post(
                "/validate",
                json={"url": "https://example.com", "advise": True, "explain": True},
                headers={"X-API-Key": key},
            )
        assert response.status_code == 200
        assert response.json()["ai_advice"] == "ADVICE-TEXT"
        assert response.json()["ai_summary"] == "SUMMARY-TEXT"
        assert len(payloads) == 2
        user_blobs = [payload["messages"][1]["content"] for payload in payloads]
        assert any("failing_checks" in blob for blob in user_blobs)
        assert any('"checks"' in blob for blob in user_blobs)


@pytest.mark.skipif(
    not os.environ.get("INCEPTION_API_KEY"),
    reason="INCEPTION_API_KEY not set (integration)",
)
class TestAdvisorIntegration:
    def test_real_inception_advice(self):
        import asyncio

        out = asyncio.run(
            ai_advisor.advise(
                url="https://example.com",
                overall="FAIL",
                summary="1/7 checks passed",
                checks=_CHECKS,
                timeout=20.0,
            )
        )
        assert out and len(out) > 10
