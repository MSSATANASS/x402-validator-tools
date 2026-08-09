"""Tests for the Qwen AI Advisor (api_server.ai_advisor) and /validate wiring."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from api_server import ai_advisor


class _Check:
    """Mimics both the internal report check (check_name) and the flattened
    CheckResultItem (name) so it can feed _flatten_checks and ai_advisor."""

    def __init__(self, name, status, message):
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
    import api_server.keystore  # noqa: F401
    import api_server.app  # noqa: F401

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
        checks = [
            _Check("manifest_discovery", "PASS", "ok"),
            _Check("caip2_compliance", "FAIL", "Payment-Required header missing"),
        ]

    return _Report()


def _mock_post(content="Fix the CAIP-2 header."):
    captured = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = str(url)
        captured["json"] = kwargs.get("json")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}}]},
            request=httpx.Request("POST", str(url)),
        )

    return fake_post, captured


_PROBE = {
    "check_name": "directory_cold_probe",
    "status": "PASS",
    "message": "ok",
    "details": {"method": "POST", "status_code": 402},
}


async def _fake_audit(url, mode, timeout=10.0):
    # _run_audit returns (report, probe) since the directory cold probe landed.
    return _fake_report(), _PROBE


class TestAdvisorUnit:
    def test_disabled_without_key(self, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        assert ai_advisor.enabled() is False

    def test_returns_none_when_disabled(self, monkeypatch):
        import asyncio

        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        assert asyncio.run(ai_advisor.advise("u", "FAIL", "s", _CHECKS)) is None

    def test_success_returns_content(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://stub.example/v1/")
        fake_post, captured = _mock_post()
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        out = asyncio.run(
            ai_advisor.advise("https://example.com", "FAIL", "1/2", _CHECKS)
        )
        assert out == "Fix the CAIP-2 header."
        assert captured["url"] == "https://stub.example/v1/chat/completions"
        user_msg = captured["json"]["messages"][1]["content"]
        assert "caip2_compliance" in user_msg
        assert "manifest_discovery" not in user_msg  # only failing checks sent

    def test_graceful_on_transport_error(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

        async def boom(self, url, **kwargs):
            raise httpx.ConnectError("nope")

        monkeypatch.setattr(httpx.AsyncClient, "post", boom)
        assert asyncio.run(ai_advisor.advise("u", "FAIL", "s", _CHECKS)) is None

    def test_graceful_on_bad_payload(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

        async def bad(self, url, **kwargs):
            return httpx.Response(
                200, json={"unexpected": True}, request=httpx.Request("POST", str(url))
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", bad)
        assert asyncio.run(ai_advisor.advise("u", "FAIL", "s", _CHECKS)) is None


class TestSummarizeUnit:
    def test_summarize_returns_none_when_disabled(self, monkeypatch):
        import asyncio

        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        assert asyncio.run(ai_advisor.summarize("u", "FAIL", "s", _CHECKS)) is None

    def test_summarize_sends_all_checks(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
        fake_post, captured = _mock_post("Your site mostly works.")
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        out = asyncio.run(
            ai_advisor.summarize("https://example.com", "FAIL", "1/2", _CHECKS)
        )
        assert out == "Your site mostly works."
        user_msg = captured["json"]["messages"][1]["content"]
        # The summary covers the whole report, not just the failures.
        assert "caip2_compliance" in user_msg
        assert "manifest_discovery" in user_msg
        system_msg = captured["json"]["messages"][0]["content"]
        assert system_msg == ai_advisor._SUMMARY_SYSTEM
        assert system_msg != ai_advisor._SYSTEM

    def test_summarize_graceful_on_transport_error(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

        async def boom(self, url, **kwargs):
            raise httpx.ConnectError("nope")

        monkeypatch.setattr(httpx.AsyncClient, "post", boom)
        assert asyncio.run(ai_advisor.summarize("u", "FAIL", "s", _CHECKS)) is None


class TestValidateWiring:
    def test_advise_attaches_advice(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
        fake_post, captured = _mock_post("Rotate your manifest.")
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            r = tc.post(
                "/validate",
                json={"url": "https://example.com", "advise": True},
                headers={"X-API-Key": key},
            )
        assert r.status_code == 200
        assert r.json()["ai_advice"] == "Rotate your manifest."
        assert "caip2_compliance" in captured["json"]["messages"][1]["content"]

    def test_no_advice_by_default(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
        fake_post, _ = _mock_post()
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            r = tc.post(
                "/validate",
                json={"url": "https://example.com"},
                headers={"X-API-Key": key},
            )
        assert r.status_code == 200
        assert r.json()["ai_advice"] is None
        assert r.json()["ai_summary"] is None

    def test_advise_without_key_is_null(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            r = tc.post(
                "/validate",
                json={"url": "https://example.com", "advise": True},
                headers={"X-API-Key": key},
            )
        assert r.status_code == 200
        assert r.json()["ai_advice"] is None


    def test_explain_attaches_summary(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
        fake_post, captured = _mock_post("In short: mostly works.")
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            r = tc.post(
                "/validate",
                json={"url": "https://example.com", "explain": True},
                headers={"X-API-Key": key},
            )
        assert r.status_code == 200
        assert r.json()["ai_summary"] == "In short: mostly works."
        assert r.json()["ai_advice"] is None
        # Summary payload includes passing checks too.
        assert "manifest_discovery" in captured["json"]["messages"][1]["content"]

    def test_explain_without_key_is_null(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            r = tc.post(
                "/validate",
                json={"url": "https://example.com", "explain": True},
                headers={"X-API-Key": key},
            )
        assert r.status_code == 200
        assert r.json()["ai_summary"] is None

    def test_advise_and_explain_both_fire(self, client, monkeypatch):
        tc, app_mod = client
        key = app_mod.get_store().issue("pro")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
        payloads = []

        async def fake_post(self, url, **kwargs):
            payload = kwargs.get("json")
            payloads.append(payload)
            is_advisor = payload["messages"][0]["content"] == ai_advisor._SYSTEM
            content = "ADVICE-TEXT" if is_advisor else "SUMMARY-TEXT"
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": content}}]},
                request=httpx.Request("POST", str(url)),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
        with patch.object(app_mod, "_run_audit", _fake_audit):
            r = tc.post(
                "/validate",
                json={
                    "url": "https://example.com",
                    "advise": True,
                    "explain": True,
                },
                headers={"X-API-Key": key},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ai_advice"] == "ADVICE-TEXT"
        assert body["ai_summary"] == "SUMMARY-TEXT"
        assert len(payloads) == 2
        user_blobs = [p["messages"][1]["content"] for p in payloads]
        assert any("failing_checks" in b for b in user_blobs)
        assert any('"checks"' in b for b in user_blobs)


@pytest.mark.skipif(
    not os.environ.get("DASHSCOPE_API_KEY"),
    reason="DASHSCOPE_API_KEY not set (integration)",
)
class TestAdvisorIntegration:
    def test_real_qwen_advice(self):
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

    def test_real_qwen_summary(self):
        import asyncio

        out = asyncio.run(
            ai_advisor.summarize(
                url="https://example.com",
                overall="FAIL",
                summary="1/7 checks passed",
                checks=_CHECKS,
                timeout=20.0,
            )
        )
        assert out and len(out) > 10
