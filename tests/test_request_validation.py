"""Strict POST payload validation for /validate, /audit-public, /admin/keys."""

from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api_server.models import IssueKeyRequest, ValidateRequest


class TestValidateRequestModel:
    def test_accepts_https_and_strips(self):
        r = ValidateRequest(url="  https://merchant.example.com/pay  ", mode="marketplace")
        assert r.url == "https://merchant.example.com/pay"
        assert r.mode == "marketplace"

    def test_rejects_missing_scheme(self):
        with pytest.raises(ValidationError) as ei:
            ValidateRequest(url="merchant.example.com")
        assert "http or https" in str(ei.value).lower() or "scheme" in str(ei.value).lower()

    def test_rejects_ftp(self):
        with pytest.raises(ValidationError):
            ValidateRequest(url="ftp://files.example.com/x")

    def test_rejects_no_host(self):
        with pytest.raises(ValidationError):
            ValidateRequest(url="https://")

    def test_rejects_whitespace_inside(self):
        with pytest.raises(ValidationError):
            ValidateRequest(url="https://example.com/has space")

    def test_rejects_unknown_mode(self):
        with pytest.raises(ValidationError):
            ValidateRequest(url="https://example.com", mode="turbo")

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            ValidateRequest(url="https://example.com", hacker="yes")  # type: ignore[call-arg]

    def test_default_mode_standard(self):
        r = ValidateRequest(url="http://localhost:8000/")
        assert r.mode == "standard"


class TestIssueKeyRequestModel:
    def test_accepts_known_plans(self):
        for pid in ("free", "pro", "enterprise"):
            assert IssueKeyRequest(plan_id=pid).plan_id == pid  # type: ignore[arg-type]

    def test_rejects_unknown_plan(self):
        with pytest.raises(ValidationError):
            IssueKeyRequest(plan_id="platinum")  # type: ignore[arg-type]

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            IssueKeyRequest(plan_id="pro", note="x")  # type: ignore[call-arg]


class TestValidateEndpointValidation:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
        monkeypatch.setenv("ADMIN_SECRET", "test-admin-secret")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        import api_server.app  # force load
        import api_server.keystore  # force load

        keystore_mod = sys.modules["api_server.keystore"]
        app_mod = sys.modules["api_server.app"]
        importlib.reload(keystore_mod)
        importlib.reload(app_mod)
        app_mod.get_store().issue("pro")
        return TestClient(app_mod.app), app_mod

    def _pro_key(self, app_mod):
        return next(k for k, p in app_mod.get_store().all().items() if p == "pro")

    def test_validate_rejects_bad_url(self, client):
        c, app_mod = client
        r = c.post(
            "/validate",
            json={"url": "not-a-url"},
            headers={"X-API-Key": self._pro_key(app_mod)},
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, list) and detail

    def test_validate_rejects_bad_mode(self, client):
        c, app_mod = client
        r = c.post(
            "/validate",
            json={"url": "https://example.com", "mode": "ultra"},
            headers={"X-API-Key": self._pro_key(app_mod)},
        )
        assert r.status_code == 422

    def test_validate_rejects_extra_json_field(self, client):
        c, app_mod = client
        r = c.post(
            "/validate",
            json={"url": "https://example.com", "extra": 1},
            headers={"X-API-Key": self._pro_key(app_mod)},
        )
        assert r.status_code == 422

    def test_audit_public_rejects_bad_url(self, client, monkeypatch):
        from api_server import ratelimit as rl_mod

        c, _app = client
        rl_mod.reset_limiter()
        r = c.post("/audit-public", json={"url": "javascript:alert(1)"})
        assert r.status_code == 422

    def test_admin_issue_rejects_unknown_plan(self, client):
        c, _ = client
        r = c.post(
            "/admin/keys",
            json={"plan_id": "platinum"},
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert r.status_code == 422

    def test_admin_issue_rejects_extra_field(self, client):
        c, _ = client
        r = c.post(
            "/admin/keys",
            json={"plan_id": "pro", "note": "nope"},
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert r.status_code == 422

    def test_validate_accepts_valid_after_strict_checks(self, client):
        c, app_mod = client

        class _Check:
            def __init__(self):
                self.check_name = "manifest_discovery"
                self.status = "PASS"
                self.message = "ok"
                self.details = None

        class _Report:
            target_url = "https://example.com"
            overall_status = "PASS"
            summary = "1/1"
            timestamp = datetime(2026, 8, 9, tzinfo=timezone.utc)

            def __init__(self):
                self.checks = [_Check()]

        async def fake_pipeline(url, mode="standard", **_kw):
            probe = {
                "check_name": "directory_cold_probe",
                "status": "PASS",
                "message": "ok",
                "details": {},
            }
            batch = {
                "check_name": "batch_settlement_requirements",
                "status": "PASS",
                "message": "n/a",
                "details": {"applicable": False},
            }
            return _Report(), probe, batch

        with patch.object(app_mod, "_run_audit", side_effect=fake_pipeline):
            r = c.post(
                "/validate",
                json={"url": "https://example.com", "mode": "standard"},
                headers={"X-API-Key": self._pro_key(app_mod)},
            )
        assert r.status_code == 200
        assert r.json()["overall"] == "PASS"
