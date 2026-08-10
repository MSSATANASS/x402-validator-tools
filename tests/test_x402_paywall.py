"""x402 paywall: challenge shape, middleware 402-before-body, dual access."""

from __future__ import annotations

import base64
import importlib
import json
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api_server import x402_paywall


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """App client with paywall enabled (X402_PAY_TO set)."""
    monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
    monkeypatch.setenv("ADMIN_SECRET", "test-admin-secret")
    monkeypatch.setenv("X402_PAY_TO", "0x1111111111111111111111111111111111111111")
    monkeypatch.setenv("X402_AMOUNT_ATOMIC", "20000")
    monkeypatch.setenv("X402_PRICE_USD", "0.02")
    monkeypatch.setenv("PUBLIC_URL", "https://x402-validator-tools.onrender.com")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("X402_FACILITATOR_URL", raising=False)

    import api_server.keystore  # noqa: F401
    import api_server.app  # noqa: F401

    keystore_mod = sys.modules["api_server.keystore"]
    app_mod = sys.modules["api_server.app"]
    importlib.reload(keystore_mod)
    importlib.reload(app_mod)
    app_mod.app.openapi_schema = None
    app_mod.get_store().issue("pro")
    return TestClient(app_mod.app)


class TestChallenge:
    def test_build_payment_required_v2(self, client: TestClient):
        body = x402_paywall.build_payment_required(path="/validate")
        assert body["x402Version"] == 2
        assert body["accepts"]
        acc = body["accepts"][0]
        assert acc["scheme"] == "exact"
        assert acc["network"] == "eip155:8453"
        assert acc["amount"] == "20000"
        assert acc["payTo"].startswith("0x")
        assert acc["asset"].startswith("0x")
        assert "validate" in body["resource"]["url"]

    def test_encode_roundtrip(self, client: TestClient):
        body = x402_paywall.build_payment_required()
        token = x402_paywall.encode_payment_required(body)
        raw = base64.b64decode(token + "=" * (-len(token) % 4))
        assert json.loads(raw)["x402Version"] == 2


class TestMiddleware402:
    def test_validate_without_auth_returns_402(self, client: TestClient):
        # No body — must not 422 before 402
        r = client.post("/validate")
        assert r.status_code == 402
        assert "payment-required" in {k.lower() for k in r.headers.keys()}
        pr = r.headers.get("payment-required") or r.headers.get("PAYMENT-REQUIRED")
        assert pr
        decoded = x402_paywall.decode_b64_json(pr)
        assert decoded is not None
        assert decoded["accepts"][0]["amount"] == "20000"
        assert r.json()["x402Version"] == 2

    def test_validate_empty_json_still_402(self, client: TestClient):
        r = client.post(
            "/validate",
            content=b"{}",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 402

    def test_validate_with_api_key_still_works(self, client: TestClient):
        from api_server.keystore import get_store

        key = next(iter(get_store().all()))
        async def fake_run(*_a, **_k):
            raise RuntimeError("stop-before-engine")

        with patch("api_server.app._run_audit", side_effect=fake_run):
            r = client.post(
                "/validate",
                headers={"X-API-Key": key},
                json={"url": "https://merchant.example/pay"},
            )
        # 502 from stubbed engine means we passed auth + body validation
        assert r.status_code == 502


class TestOpenApiPaid:
    def test_validate_marked_paid(self, client: TestClient):
        body = client.get("/openapi.json").json()
        op = body["paths"]["/validate"]["post"]
        assert "x-payment-info" in op
        assert op["x-payment-info"]["price"]["mode"] == "fixed"
        assert "402" in op["responses"]
        assert op["security"]  # dual access, not empty-only

    def test_free_routes_still_empty_security(self, client: TestClient):
        paths = client.get("/openapi.json").json()["paths"]
        assert paths["/health"]["get"]["security"] == []
        assert paths["/audit-public"]["post"]["security"] == []

    def test_well_known_x402(self, client: TestClient):
        r = client.get("/.well-known/x402")
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 1
        assert any("/validate" in u for u in body["resources"])
        assert body["openapi"].endswith("/openapi.json")
