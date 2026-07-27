"""Tests for the FastAPI app: routes, models, stripe stub."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    """Reload the app module for each test so api_keys changes don't leak."""
    import importlib
    import api_server.app as app_mod
    importlib.reload(app_mod)
    app_mod.api_keys["test-key-free"] = "free"
    app_mod.api_keys["test-key-pro"] = "pro"
    return TestClient(app_mod.app)


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


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
    def test_requires_api_key(self, client: TestClient) -> None:
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
                from datetime import datetime, timezone
                self.timestamp = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
                self.checks = [
                    _Check("manifest_discovery", "PASS", "ok", None),
                ]

        async def fake_run_audit(url: str, mode: str = "standard", **_kw):
            return _Report()

        with patch("x402_validator._engine.run_audit", side_effect=fake_run_audit):
            r = client.post(
                "/validate",
                json={"url": "https://example.com", "mode": "standard"},
                headers={"X-API-Key": "test-key-pro"},
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
        # No STRIPE_SECRET_KEY in env → note about unconfigured
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
