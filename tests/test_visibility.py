"""Tests for the directory cold-probe check (api_server.visibility).

Unit tests are DB-free and use ``httpx.MockTransport``. Integration tests
verify that ``/validate`` and ``/audit-public`` append the probe result to
their ``checks[]`` arrays (probe patched with a fixed result).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from api_server import visibility
from api_server.visibility import ResponseSnapshot

TARGET = "https://merchant.example.com/api"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _probe(
    status_code: int | None = None,
    exc: Exception | None = None,
    *,
    body: str = "",
    headers: dict[str, str] | None = None,
):
    """Run the cold probe against a MockTransport returning ``status_code``
    (or raising ``exc``); return ``(result, seen_request)``."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        if exc is not None:
            raise exc
        return httpx.Response(
            status_code, content=body.encode("utf-8"), headers=headers or {}
        )

    transport = httpx.MockTransport(handler)
    result = asyncio.run(
        visibility.check_directory_cold_probe(TARGET, timeout=5.0, transport=transport)
    )
    return result, seen.get("request")


def _run_probe(
    status_code: int | None = None,
    exc: Exception | None = None,
    *,
    body: str = "",
    headers: dict[str, str] | None = None,
):
    """Like ``_probe`` but via ``run_directory_cold_probe`` (result + snapshot)."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        if exc is not None:
            raise exc
        return httpx.Response(
            status_code, content=body.encode("utf-8"), headers=headers or {}
        )

    transport = httpx.MockTransport(handler)
    result, snapshot = asyncio.run(
        visibility.run_directory_cold_probe(
            TARGET, timeout=5.0, transport=transport
        )
    )
    return result, snapshot, seen.get("request")


# ---------------------------------------------------------------------------
# Unit: CheckResult shape + status matrix
# ---------------------------------------------------------------------------


class TestCheckResultShape:
    def test_result_shape(self) -> None:
        result, _ = _probe(402)
        assert set(result.keys()) == {"check_name", "status", "message", "details"}
        assert result["check_name"] == "directory_cold_probe"
        assert set(result["details"].keys()) == {"method", "status_code"}
        assert result["details"]["method"] == "POST"

    def test_probe_is_bare_post(self) -> None:
        """Directories probe with a bare POST: no body, no auth, no payment."""
        result, request = _probe(402)
        assert result["status"] == "PASS"
        assert request is not None
        assert request.method == "POST"
        assert request.content == b""
        lowered = {k.lower() for k in request.headers}
        assert "x-payment" not in lowered
        assert "authorization" not in lowered


class TestStatusMatrix:
    def test_402_is_pass(self) -> None:
        result, _ = _probe(402)
        assert result["status"] == "PASS"
        assert result["details"]["status_code"] == 402

    def test_400_fails_body_validation_before_gate(self) -> None:
        result, _ = _probe(400)
        assert result["status"] == "FAIL"
        assert result["details"]["status_code"] == 400
        assert "body validation" in result["message"]
        assert "before the payment gate" in result["message"]

    def test_500_fails_body_validation_before_gate(self) -> None:
        result, _ = _probe(500)
        assert result["status"] == "FAIL"
        assert result["details"]["status_code"] == 500
        assert "body validation" in result["message"]
        assert "before the payment gate" in result["message"]

    def test_401_fails_auth_gate(self) -> None:
        result, _ = _probe(401)
        assert result["status"] == "FAIL"
        assert result["details"]["status_code"] == 401
        assert "auth gate" in result["message"]

    def test_403_fails_auth_gate(self) -> None:
        result, _ = _probe(403)
        assert result["status"] == "FAIL"
        assert result["details"]["status_code"] == 403
        assert "auth gate" in result["message"]

    def test_405_fails_post_not_allowed(self) -> None:
        result, _ = _probe(405)
        assert result["status"] == "FAIL"
        assert result["details"]["status_code"] == 405
        assert "POST is not allowed" in result["message"]

    def test_200_fails_no_gate_for_method(self) -> None:
        result, _ = _probe(200)
        assert result["status"] == "FAIL"
        assert result["details"]["status_code"] == 200
        assert "payment gate for this method" in result["message"]

    def test_each_class_has_its_own_message(self) -> None:
        messages = {code: _probe(code)[0]["message"] for code in (400, 401, 405, 200)}
        assert len(set(messages.values())) == 4


# ---------------------------------------------------------------------------
# Unit: ERROR path + never-raise contract
# ---------------------------------------------------------------------------


class TestErrorPath:
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("connection refused"),
            httpx.ReadTimeout("timed out"),
        ],
    )
    def test_network_errors_are_error_status(self, exc) -> None:
        result, _ = _probe(exc=exc)
        assert result["status"] == "ERROR"
        assert result["check_name"] == "directory_cold_probe"
        assert result["details"]["status_code"] is None

    def test_never_raises_on_unexpected_error(self, monkeypatch) -> None:
        async def boom(self, url, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(httpx.AsyncClient, "post", boom)
        result = asyncio.run(visibility.check_directory_cold_probe(TARGET))
        assert result["status"] == "ERROR"
        assert result["check_name"] == "directory_cold_probe"


# ---------------------------------------------------------------------------
# Unit: ResponseSnapshot from run_directory_cold_probe
# ---------------------------------------------------------------------------


class TestResponseSnapshot:
    def test_402_fills_snapshot_with_body_and_headers(self) -> None:
        payload = '{"x402Version":2,"accepts":[]}'
        result, snap, _ = _run_probe(
            402,
            body=payload,
            headers={"Content-Type": "application/json", "X-Payment-Required": "1"},
        )
        assert result["status"] == "PASS"
        assert snap is not None
        assert isinstance(snap, visibility.ResponseSnapshot)
        assert snap.status_code == 402
        assert snap.body == payload
        # httpx may normalize header names; values must be present
        lowered = {k.lower(): v for k, v in snap.headers.items()}
        assert lowered.get("content-type") == "application/json"
        assert lowered.get("x-payment-required") == "1"

    def test_non_402_http_still_fills_snapshot(self) -> None:
        result, snap, _ = _run_probe(400, body="bad request")
        assert result["status"] == "FAIL"
        assert snap is not None
        assert snap.status_code == 400
        assert snap.body == "bad request"

    def test_transport_error_snapshot_is_none(self) -> None:
        result, snap, _ = _run_probe(exc=httpx.ConnectError("connection refused"))
        assert result["status"] == "ERROR"
        assert snap is None

    def test_timeout_snapshot_is_none(self) -> None:
        result, snap, _ = _run_probe(exc=httpx.ReadTimeout("timed out"))
        assert result["status"] == "ERROR"
        assert snap is None

    def test_wrapper_returns_only_dict(self) -> None:
        result, _ = _probe(402)
        assert isinstance(result, dict)
        assert "check_name" in result


# ---------------------------------------------------------------------------
# Integration: /validate and /audit-public include the probe in checks[]
# ---------------------------------------------------------------------------


def _fake_probe_result(status: str = "FAIL", status_code: int | None = 400) -> dict:
    return {
        "check_name": "directory_cold_probe",
        "status": status,
        "message": "stub probe result",
        "details": {"method": "POST", "status_code": status_code},
    }


def _make_fake_audit_report():
    class _Check:
        check_name = "manifest_discovery"
        status = "PASS"
        message = "ok"
        details = None

    class _Report:
        target_url = "https://example.com"
        overall_status = "PASS"
        summary = "1/1 checks passed"
        timestamp = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
        checks = [_Check()]

    return _Report()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Reload the app module for each test and point KeyStore at tmp."""
    import importlib
    import sys

    monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
    monkeypatch.setenv("ADMIN_SECRET", "test-admin-secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import api_server.keystore  # noqa: F401
    import api_server.app  # noqa: F401

    keystore_mod = sys.modules["api_server.keystore"]
    app_mod = sys.modules["api_server.app"]
    importlib.reload(keystore_mod)
    importlib.reload(app_mod)
    app_mod.get_store().issue("pro")
    return TestClient(app_mod.app)


class TestValidateIncludesProbe:
    def test_checks_include_directory_cold_probe(self, client: TestClient) -> None:
        from api_server.keystore import get_store

        pro_key = next(k for k, p in get_store().all().items() if p == "pro")
        probe = _fake_probe_result()

        async def fake_run_audit(url: str, mode: str = "standard", **_kw):
            return _make_fake_audit_report()

        async def fake_run_probe(url, timeout=10.0, **_kw):
            # Non-402 body → batch GET fallback; patch GET to avoid network.
            return probe, ResponseSnapshot(status_code=400, headers={}, body="")

        async def boom_get(self, url, **_kw):
            raise RuntimeError("no network in tests")

        with patch(
            "x402_conformance_suite._engine.run_audit", side_effect=fake_run_audit
        ), patch(
            "api_server.visibility.run_directory_cold_probe", side_effect=fake_run_probe
        ), patch(
            "httpx.AsyncClient.get", boom_get
        ):
            r = client.post(
                "/validate",
                json={"url": "https://example.com", "mode": "standard"},
                headers={"X-API-Key": pro_key},
            )
        assert r.status_code == 200
        checks = r.json()["checks"]
        by_name = {c["name"]: c for c in checks}
        assert "manifest_discovery" in by_name  # engine checks intact
        assert by_name["directory_cold_probe"] == {
            "name": "directory_cold_probe",
            "status": "FAIL",
            "message": "stub probe result",
            "details": {"method": "POST", "status_code": 400},
        }
        assert "batch_settlement_requirements" in by_name


class TestAuditPublicIncludesProbe:
    def test_checks_include_directory_cold_probe(self, client: TestClient) -> None:
        import json
        from api_server import ratelimit as rl_mod

        rl_mod.reset_limiter()
        probe = _fake_probe_result(status="PASS", status_code=402)
        exact_body = json.dumps(
            {
                "x402Version": 2,
                "accepts": [
                    {
                        "scheme": "exact",
                        "network": "eip155:8453",
                        "amount": "1000",
                        "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                        "payTo": "0x1111111111111111111111111111111111111111",
                    }
                ],
            }
        )

        async def fake_run_audit(url: str, mode: str = "standard", **_kw):
            return _make_fake_audit_report()

        async def fake_run_probe(url, timeout=10.0, **_kw):
            return probe, ResponseSnapshot(
                status_code=402, headers={}, body=exact_body
            )

        with patch(
            "x402_conformance_suite._engine.run_audit", side_effect=fake_run_audit
        ), patch(
            "api_server.visibility.run_directory_cold_probe", side_effect=fake_run_probe
        ):
            r = client.post(
                "/audit-public", json={"url": "https://example.com"}
            )
        assert r.status_code == 200
        checks = r.json()["checks"]
        by_name = {c["name"]: c for c in checks}
        assert "manifest_discovery" in by_name  # engine checks intact
        assert by_name["directory_cold_probe"] == {
            "name": "directory_cold_probe",
            "status": "PASS",
            "message": "stub probe result",
            "details": {"method": "POST", "status_code": 402},
        }
        assert by_name["batch_settlement_requirements"]["status"] == "PASS"
        assert by_name["batch_settlement_requirements"]["details"]["applicable"] is False
