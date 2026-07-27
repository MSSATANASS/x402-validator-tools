"""Tests for the FastAPI app: routes, models, stripe stub."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Reload the app module for each test and point KeyStore at tmp."""
    import importlib
    import sys
    monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
    monkeypatch.setenv("ADMIN_SECRET", "test-admin-secret")
    # Force-load the modules first so they're registered in sys.modules
    import api_server.keystore  # noqa: F401
    import api_server.app  # noqa: F401
    keystore_mod = sys.modules["api_server.keystore"]
    app_mod = sys.modules["api_server.app"]
    importlib.reload(keystore_mod)
    importlib.reload(app_mod)
    # Mint a key
    app_mod.get_store().issue("pro")
    return TestClient(app_mod.app)


def _make_fake_audit_report():
    class _Check:
        def __init__(self, name, status, message, details=None):
            self.check_name = name
            self.status = status
            self.message = message
            self.details = details

    class _Report:
        def __init__(self):
            self.target_url = "https://example.com"
            self.overall_status = "PASS"
            self.summary = "4/4 checks passed"
            self.timestamp = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
            self.checks = [_Check("manifest_discovery", "PASS", "ok", None)]

    return _Report()


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestLanding:
    def test_renders_html(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "x402 Validator" in r.text
        assert "$9" in r.text
        assert "/create-checkout-session" in r.text


class TestPlans:
    def test_lists_all_three(self, client: TestClient) -> None:
        r = client.get("/plans")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()}
        assert ids == {"free", "pro", "enterprise"}

    def test_pro_has_stripe_price(self, client: TestClient) -> None:
        r = client.get("/plans")
        plans = {p["id"]: p for p in r.json()}
        assert plans["pro"]["stripe_price_id"].startswith("price_")
        assert plans["pro"]["price_cents"] == 900
        assert plans["enterprise"]["price_cents"] == 4900


class TestValidate:
    def test_requires_api_key_header(self, client: TestClient) -> None:
        r = client.post("/validate", json={"url": "https://example.com"})
        assert r.status_code == 422  # FastAPI: missing required header

    def test_rejects_unknown_key(self, client: TestClient) -> None:
        r = client.post(
            "/validate",
            json={"url": "https://example.com"},
            headers={"X-API-Key": "bogus"},
        )
        assert r.status_code == 401

    def test_runs_audit_with_valid_key(self, client: TestClient) -> None:
        # Find the key that the fixture minted
        from api_server.keystore import get_store
        keys = [
            k for k, p in get_store().all().items() if p == "pro"
        ]
        assert keys, "fixture should have minted a pro key"
        pro_key = keys[0]

        async def fake_run_audit(url: str, mode: str = "standard", **_kw):
            return _make_fake_audit_report()

        with patch("x402_validator._engine.run_audit", side_effect=fake_run_audit):
            r = client.post(
                "/validate",
                json={"url": "https://example.com", "mode": "standard"},
                headers={"X-API-Key": pro_key},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["url"] == "https://example.com"
        assert body["overall"] == "PASS"
        assert body["checks"][0]["name"] == "manifest_discovery"
        assert body["latency_ms"] is not None


class TestCheckoutSession:
    def test_unknown_plan_rejected(self, client: TestClient) -> None:
        r = client.post("/create-checkout-session?plan_id=nope")
        assert r.status_code == 400

    def test_free_plan_no_checkout(self, client: TestClient) -> None:
        r = client.post("/create-checkout-session?plan_id=free")
        assert r.status_code == 200
        assert r.json()["note"] is not None
        assert r.json()["checkout_url"] is None

    def test_paid_plan_without_stripe(self, client: TestClient) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STRIPE_SECRET_KEY", None)
            r = client.post("/create-checkout-session?plan_id=pro")
        body = r.json()
        assert body["checkout_url"] is None
        assert "not configured" in body["note"].lower() or "no checkout" in body["note"].lower()


class TestStripeIntegration:
    def test_create_checkout_session_returns_none_for_free(self) -> None:
        from api_server import stripe_integration
        url = stripe_integration.create_checkout_session(
            "free",
            success_url="https://x/success",
            cancel_url="https://x/cancel",
        )
        assert url is None

    def test_create_checkout_session_raises_on_unknown(self) -> None:
        from api_server import stripe_integration
        with pytest.raises(ValueError):
            stripe_integration.create_checkout_session(
                "bogus",
                success_url="https://x/success",
                cancel_url="https://x/cancel",
            )

    def test_create_checkout_session_returns_none_without_secret(self) -> None:
        from api_server import stripe_integration
        stripe_integration._stripe = None  # reset lazy import
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STRIPE_SECRET_KEY", None)
            url = stripe_integration.create_checkout_session(
                "pro",
                success_url="https://x/success",
                cancel_url="https://x/cancel",
            )
        assert url is None


class TestKeyStore:
    def test_issue_persists_to_disk(self, tmp_path) -> None:
        from api_server.keystore import KeyStore
        store = KeyStore(tmp_path / "ks.json")
        token = store.issue("pro")
        assert store.get(token) == "pro"
        # Reload from disk
        store2 = KeyStore(tmp_path / "ks.json")
        assert store2.get(token) == "pro"

    def test_revoke_removes(self, tmp_path) -> None:
        from api_server.keystore import KeyStore
        store = KeyStore(tmp_path / "ks.json")
        token = store.issue("pro")
        assert store.revoke(token) is True
        assert store.revoke(token) is False  # second call idempotent

    def test_empty_file_returns_empty(self, tmp_path) -> None:
        from api_server.keystore import KeyStore
        store = KeyStore(tmp_path / "missing.json")
        assert store.all() == {}


class TestAdminEndpoints:
    def test_issue_requires_admin_secret(self, client: TestClient) -> None:
        r = client.post("/admin/keys", json={"plan_id": "pro"})
        assert r.status_code == 422

    def test_issue_wrong_secret(self, client: TestClient) -> None:
        r = client.post(
            "/admin/keys",
            json={"plan_id": "pro"},
            headers={"X-Admin-Secret": "wrong"},
        )
        assert r.status_code == 401

    def test_issue_mints_key(self, client: TestClient) -> None:
        r = client.post(
            "/admin/keys",
            json={"plan_id": "pro"},
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["plan_id"] == "pro"
        assert body["api_key"].startswith("eyJ") or len(body["api_key"]) > 20

    def test_list_keys(self, client: TestClient) -> None:
        r = client.get(
            "/admin/keys",
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_revoke_key(self, client: TestClient) -> None:
        # First issue one
        r = client.post(
            "/admin/keys",
            json={"plan_id": "pro"},
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        key = r.json()["api_key"]
        # Then revoke
        r = client.delete(
            f"/admin/keys/{key}",
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert r.status_code == 200
        assert r.json()["revoked"] is True

    def test_revoke_unknown_key(self, client: TestClient) -> None:
        r = client.delete(
            "/admin/keys/never-issued-key",
            headers={"X-Admin-Secret": "test-admin-secret"},
        )
        assert r.status_code == 404

    def test_full_flow_issued_key_works_for_validate(self, client: TestClient) -> None:
        async def fake_run_audit(url: str, mode: str = "standard", **_kw):
            return _make_fake_audit_report()

        with patch("x402_validator._engine.run_audit", side_effect=fake_run_audit):
            r = client.post(
                "/admin/keys",
                json={"plan_id": "enterprise"},
                headers={"X-Admin-Secret": "test-admin-secret"},
            )
            assert r.status_code == 200
            new_key = r.json()["api_key"]

            r = client.post(
                "/validate",
                json={"url": "https://example.com"},
                headers={"X-API-Key": new_key},
            )
            assert r.status_code == 200


class TestSuccessCancel:
    def test_success_page(self, client: TestClient) -> None:
        r = client.get("/success")
        assert r.status_code == 200
        assert "Payment received" in r.text

    def test_cancel_page(self, client: TestClient) -> None:
        r = client.get("/cancel")
        assert r.status_code == 200
        assert "cancelled" in r.text.lower()
