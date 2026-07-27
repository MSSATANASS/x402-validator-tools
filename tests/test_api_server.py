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
        # Headline copy (motion-style spec)
        assert "Ship x402 endpoints with confidence" in r.text  # pre-headline (serif)
        assert "Audit x402 in Seconds" in r.text  # main headline (gradient)
        assert "operator-actionable errors" in r.text  # subheadline copy
        # Pricing plans rendered
        assert "$9" in r.text
        assert "$49" in r.text
        # Stripe checkout links intact
        assert "/create-checkout-session?plan_id=pro" in r.text
        assert "/create-checkout-session?plan_id=enterprise" in r.text
        assert "/create-checkout-session?plan_id=free" in r.text
        # Two design-system fonts loaded from Google Fonts (replaces Geist/General Sans)
        assert "Instrument+Sans" in r.text
        assert "Instrument+Serif" in r.text
        assert "fonts.googleapis.com" in r.text
        # Old theme system (purple/HSL) is fully gone
        assert "260 87% 3%" not in r.text
        assert "General Sans" not in r.text
        assert "Geist" not in r.text
        # Old MotionSites pieces are gone
        assert ".liquid-glass" not in r.text
        assert "mask-composite" not in r.text
        for name in ("Asterpay", "Hugen", "Observer", "Greeneris", "SmartFlow"):
            assert name not in r.text
        # Dark-mode spec markers
        assert "#000000" in r.text
        assert "#3054ff" in r.text
        assert "#b4c0ff" in r.text
        # Hero video slot present
        assert "hero-video-wrap" in r.text
        assert "hls.js" in r.text
        assert "stream.mux.com" in r.text
        assert "images.unsplash.com" in r.text
        # Decorative gradients
        assert "rgba(30,58,138,0.20)" in r.text
        assert "rgba(49,46,129,0.20)" in r.text
        assert "blur(120px)" in r.text
        assert "mix-blend-mode: screen" in r.text
        # Hero CTAs
        assert "Get Your API Key" in r.text
        assert "Try It Free" in r.text
        # Animations
        assert "@keyframes fadeUp" in r.text
        assert "@keyframes scaleIn" in r.text
        # Most popular ribbon kept on Pro pricing card
        assert "Most popular" in r.text
        # Stripped placeholder substitutions
        assert "__SVG_MANIFEST__" not in r.text

        # ----- Aggressive — audit demo, FAQ, SEO, OG -----
        # Public demo form wired to /audit-public
        assert 'id="auditForm"' in r.text
        assert 'id="auditResults"' in r.text
        assert "fillUrl" in r.text  # helper exposed for inline sample links
        # Front-end handler posts to /audit-public
        assert "/audit-public" in r.text
        # Rate-limit hint present
        assert "5 audits per IP per day" in r.text or "5/IP/day" in r.text
        # FAQ section
        assert 'id="faq"' in r.text
        assert "<details" in r.text
        assert "x402 conformance" in r.text
        # JSON-LD structured data
        assert "application/ld+json" in r.text
        assert '"@type":"SoftwareApplication"' in r.text or '"@type": "SoftwareApplication"' in r.text
        assert '"@type":"FAQPage"' in r.text or '"@type": "FAQPage"' in r.text
        assert "Gael L Chulim" in r.text
        assert '"priceCurrency":"USD"' in r.text or '"priceCurrency": "USD"' in r.text
        # Open Graph + Twitter card meta
        assert 'property="og:title"' in r.text
        assert 'property="og:description"' in r.text
        assert 'name="twitter:card"' in r.text
        assert 'rel="canonical"' in r.text
        # Favicon (data URL svg)
        assert 'rel="icon"' in r.text
        # Robots
        assert 'name="robots"' in r.text
        # Section anchors so navbar Pricing scrolls
        assert 'id="pricing"' in r.text
        assert 'id="audit"' in r.text
        # og:image now points to a real generated asset served from /static
        assert 'property="og:image" content="https://lastminutestickets.com/static/og-image.png"' in r.text
        assert 'name="twitter:image"' in r.text
        # Real PNG favicon + apple-touch-icon (replaces the old inline SVG data URI)
        assert 'rel="icon" type="image/png" href="/static/favicon-32.png"' in r.text
        assert 'rel="apple-touch-icon" href="/static/apple-touch-icon.png"' in r.text
        # Navbar uses the real logo mark image, not a generic sunburst icon
        assert '/static/logo-mark-512.png' in r.text
        # Navbar only links to sections/routes that actually exist —
        # no dead links to #stories, /docs, or a non-existent "Book A Demo"
        assert "#stories" not in r.text
        assert 'href="/docs"' not in r.text
        assert "Book A Demo" not in r.text
        assert "Customer Stories" not in r.text
        assert 'href="#audit">Try It Free</a>' in r.text
        assert 'href="#faq">FAQ</a>' in r.text
        assert 'href="/health">Status</a>' in r.text
        # Pricing headline no longer repeats the "at the speed of thought" tic
        assert "at the speed of thought" not in r.text


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

    def test_get_unknown_plan_rejected(self, client: TestClient) -> None:
        r = client.get("/create-checkout-session?plan_id=nope", follow_redirects=False)
        assert r.status_code == 400

    def test_get_free_plan_redirects_to_success(self, client: TestClient) -> None:
        r = client.get("/create-checkout-session?plan_id=free", follow_redirects=False)
        assert r.status_code in (301, 302, 303, 307, 308)
        assert "/success" in r.headers["location"]

    def test_get_paid_plan_without_stripe_returns_503(self, client: TestClient) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STRIPE_SECRET_KEY", None)
            r = client.get("/create-checkout-session?plan_id=pro", follow_redirects=False)
        assert r.status_code == 503


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


class TestKeyStoreClaims:
    def test_issue_with_session_persists_claim(self, tmp_path) -> None:
        from api_server.keystore import KeyStore
        store = KeyStore(tmp_path / "ks.json")
        token = store.issue("pro", customer_id="cus_123", session_id="cs_abc")
        claim = store.claim_by_session("cs_abc")
        assert claim is not None
        assert claim["plan_id"] == "pro"
        assert claim["api_key"] == token
        assert claim["customer_id"] == "cus_123"
        assert claim["issued_at"]  # ISO string
        assert claim["claimed_at"] is None

    def test_claim_by_session_returns_none_for_unknown(self, tmp_path) -> None:
        from api_server.keystore import KeyStore
        store = KeyStore(tmp_path / "ks.json")
        assert store.claim_by_session("nope") is None
        assert store.claim_by_session(None) is None

    def test_mark_claimed_sets_timestamp(self, tmp_path) -> None:
        from api_server.keystore import KeyStore
        store = KeyStore(tmp_path / "ks.json")
        store.issue("pro", session_id="cs_x")
        assert store.mark_claimed("cs_x") is True
        claim = store.claim_by_session("cs_x")
        assert claim["claimed_at"] is not None

    def test_mark_claimed_unknown_returns_false(self, tmp_path) -> None:
        from api_server.keystore import KeyStore
        store = KeyStore(tmp_path / "ks.json")
        assert store.mark_claimed("never") is False

    def test_revoke_drops_matching_claims(self, tmp_path) -> None:
        from api_server.keystore import KeyStore
        store = KeyStore(tmp_path / "ks.json")
        token = store.issue("pro", session_id="cs_a")
        store.issue("pro", session_id="cs_b")
        assert store.revoke(token) is True
        # Claim for the revoked key is gone; other claim remains
        assert store.claim_by_session("cs_a") is None
        assert store.claim_by_session("cs_b") is not None

    def test_load_legacy_flat_format_migrates(self, tmp_path) -> None:
        from api_server import keystore as ks_mod
        f = tmp_path / "legacy.json"
        f.write_text('{"oldtoken1": "pro", "oldtoken2": "enterprise"}')
        store = ks_mod.KeyStore(f)
        assert store.get("oldtoken1") == "pro"
        assert store.get("oldtoken2") == "enterprise"
        # Save round-trips without losing data
        store.issue("free")
        store2 = ks_mod.KeyStore(f)
        assert store2.get("oldtoken1") == "pro"
        assert store2.get("oldtoken2") == "enterprise"
        assert len(store2.all()) == 3


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


class TestStripeWebhook:
    def test_invalid_signature_returns_400(self, client: TestClient) -> None:
        with patch("api_server.stripe_integration.verify_webhook", return_value=None):
            r = client.post(
                "/stripe-webhook",
                content=b'{"type":"checkout.session.completed"}',
                headers={"stripe-signature": "bogus"},
            )
        assert r.status_code == 400

    def test_unknown_event_acked_without_key(self, client: TestClient) -> None:
        event = {"type": "customer.created", "data": {"object": {"id": "cus_x"}}}
        with patch("api_server.stripe_integration.verify_webhook", return_value=event):
            r = client.post(
                "/stripe-webhook",
                content=b"{}",
                headers={"stripe-signature": "ok"},
            )
        assert r.status_code == 200
        assert r.json()["received"] is True

    def test_checkout_completed_mints_key_and_persists_claim(self, client: TestClient) -> None:
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_1"}},
        }
        session = {
            "id": "cs_test_1",
            "customer": "cus_test_1",
            "amount_total": None,
            "subscription": "sub_test_1",
            "mode": "subscription",
            "metadata": {"plan_id": "pro"},
        }
        with patch("api_server.stripe_integration.verify_webhook", return_value=event), \
             patch("api_server.stripe_integration.retrieve_session", return_value=session):
            r = client.post(
                "/stripe-webhook",
                content=b"{}",
                headers={"stripe-signature": "ok"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["minted"] is True
        assert body["plan_id"] == "pro"
        assert body["session_id"] == "cs_test_1"
        # Claim is persisted, and the api_key is registered as valid
        claim = client.__class__ if False else None  # noqa: F841 - placeholder
        from api_server.keystore import get_store
        claim = get_store().claim_by_session("cs_test_1")
        assert claim is not None
        assert claim["plan_id"] == "pro"
        assert claim["customer_id"] == "cus_test_1"
        assert get_store().get(claim["api_key"]) == "pro"

    def test_checkout_completed_fallback_to_amount_total(self, client: TestClient) -> None:
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_test_2"}},
        }
        session = {
            "id": "cs_test_2",
            "customer": "cus_test_2",
            "amount_total": 4900,
            "subscription": None,
            "mode": "subscription",
            "metadata": {},
        }
        with patch("api_server.stripe_integration.verify_webhook", return_value=event), \
             patch("api_server.stripe_integration.retrieve_session", return_value=session):
            r = client.post(
                "/stripe-webhook",
                content=b"{}",
                headers={"stripe-signature": "ok"},
            )
        assert r.status_code == 200
        assert r.json()["plan_id"] == "enterprise"

    def test_checkout_completed_idempotent_on_duplicate_event(self, client: TestClient) -> None:
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_dup"}},
        }
        session = {
            "id": "cs_dup", "customer": "cus_dup",
            "amount_total": None, "subscription": None, "mode": "subscription",
            "metadata": {"plan_id": "pro"},
        }
        with patch("api_server.stripe_integration.verify_webhook", return_value=event), \
             patch("api_server.stripe_integration.retrieve_session", return_value=session):
            r1 = client.post(
                "/stripe-webhook", content=b"{}",
                headers={"stripe-signature": "ok"},
            )
            r2 = client.post(
                "/stripe-webhook", content=b"{}",
                headers={"stripe-signature": "ok"},
            )
        assert r1.json()["minted"] is True
        assert r2.json()["minted"] is False
        assert r2.json()["reason"] == "claim already exists"

    def test_checkout_completed_unresolvable_plan_returns_minted_false(self, client: TestClient) -> None:
        event = {
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_x"}},
        }
        session = {
            "id": "cs_x", "customer": "cus_x",
            "amount_total": 12345,
            "subscription": None, "mode": "subscription",
            "metadata": {},
        }
        with patch("api_server.stripe_integration.verify_webhook", return_value=event), \
             patch("api_server.stripe_integration.retrieve_session", return_value=session):
            r = client.post(
                "/stripe-webhook", content=b"{}",
                headers={"stripe-signature": "ok"},
            )
        assert r.status_code == 200
        assert r.json()["minted"] is False


class TestSuccessPageRender:
    def test_with_known_session_shows_key_and_plan(self, client: TestClient) -> None:
        from api_server.keystore import get_store
        token = get_store().issue("pro", customer_id="cus_a", session_id="cs_show")
        r = client.get("/success?session_id=cs_show")
        assert r.status_code == 200
        # The api_key is rendered exactly once, plan label is shown
        assert token in r.text
        assert "Pro" in r.text
        # Copy button is wired
        assert "copyBtn" in r.text
        assert "Copy key" in r.text

    def test_with_unknown_session_falls_back(self, client: TestClient) -> None:
        r = client.get("/success?session_id=never_existed")
        assert r.status_code == 200
        assert "still being issued" in r.text.lower()

    def test_without_session_falls_back(self, client: TestClient) -> None:
        r = client.get("/success")
        assert r.status_code == 200
        assert "still being issued" in r.text.lower()

    def test_marks_claim_as_claimed_on_first_view(self, client: TestClient) -> None:
        from api_server.keystore import get_store
        get_store().issue("pro", session_id="cs_mark")
        r = client.get("/success?session_id=cs_mark")
        assert r.status_code == 200
        claim = get_store().claim_by_session("cs_mark")
        assert claim["claimed_at"] is not None

    def test_revoked_key_falls_back(self, client: TestClient) -> None:
        from api_server.keystore import get_store
        token = get_store().issue("enterprise", session_id="cs_rev")
        get_store().revoke(token)
        r = client.get("/success?session_id=cs_rev")
        assert r.status_code == 200
        assert "still being issued" in r.text.lower()
        assert token not in r.text

    def test_does_not_escape_unreserved_chars_in_api_key(self, client: TestClient) -> None:
        """secrets.token_urlsafe uses URL-safe alphabet; html.escape is harmless on it."""
        from api_server.keystore import get_store
        token = get_store().issue("free", session_id="cs_fmt")
        r = client.get("/success?session_id=cs_fmt")
        assert r.status_code == 200
        # The token must appear verbatim
        assert token in r.text


class TestAuditPublic:
    def _setup_limits(self, monkeypatch, limit: int) -> None:
        from api_server import ratelimit as rl_mod
        monkeypatch.setenv("AUDIT_PUBLIC_DAILY_LIMIT", str(limit))
        rl_mod.reset_limiter()

    def test_no_key_required_runs_audit(self, client, monkeypatch) -> None:
        from api_server import ratelimit as rl_mod
        rl_mod.reset_limiter()

        async def fake_run(url, mode, **_kw):
            return _make_fake_audit_report()

        with patch("x402_validator._engine.run_audit", side_effect=fake_run):
            r = client.post(
                "/audit-public",
                json={"url": "https://x402-merchant.example.com"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["overall"] == "PASS"
        assert body["remaining_today"] == 4  # default 5, one used
        assert isinstance(body["latency_ms"], (int, float))
        assert body["checks"][0]["name"] == "manifest_discovery"

    def test_rate_limit_returns_429_after_n_calls(self, client, monkeypatch) -> None:
        self._setup_limits(monkeypatch, limit=3)

        async def fake_run(url, mode, **_kw):
            return _make_fake_audit_report()

        with patch("x402_validator._engine.run_audit", side_effect=fake_run):
            for _ in range(3):
                r = client.post("/audit-public", json={"url": "https://x.example"})
                assert r.status_code == 200
            r = client.post("/audit-public", json={"url": "https://x.example"})
        assert r.status_code == 429
        assert "limit" in r.json()["detail"].lower()
        assert "pro" in r.json()["detail"].lower()  # nudges Pro upgrade

    def test_remaining_today_decrements(self, client, monkeypatch) -> None:
        self._setup_limits(monkeypatch, limit=5)

        async def fake_run(url, mode, **_kw):
            return _make_fake_audit_report()

        with patch("x402_validator._engine.run_audit", side_effect=fake_run):
            r1 = client.post("/audit-public", json={"url": "https://x.example"})
            r2 = client.post("/audit-public", json={"url": "https://x.example"})
        assert r1.json()["remaining_today"] == 4
        assert r2.json()["remaining_today"] == 3

    def test_audit_failure_returns_502(self, client, monkeypatch) -> None:
        from api_server import ratelimit as rl_mod
        rl_mod.reset_limiter()

        async def boom(url, mode, **_kw):
            raise RuntimeError("engine died")

        with patch("x402_validator._engine.run_audit", side_effect=boom):
            r = client.post("/audit-public", json={"url": "https://x.example"})
        assert r.status_code == 502
        assert "engine died" in r.json()["detail"]


class TestRateLimiter:
    def test_under_limit_allows_then_blocks(self) -> None:
        from api_server.ratelimit import IpRateLimiter
        rl = IpRateLimiter(window_seconds=60)
        for _ in range(3):
            assert rl.allow("k", 3) is True
        assert rl.allow("k", 3) is False

    def test_remaining_counts_correctly(self) -> None:
        from api_server.ratelimit import IpRateLimiter
        rl = IpRateLimiter(window_seconds=60)
        assert rl.remaining("k", 5) == 5
        rl.allow("k", 5)
        rl.allow("k", 5)
        assert rl.remaining("k", 5) == 3

    def test_independent_keys(self) -> None:
        from api_server.ratelimit import IpRateLimiter
        rl = IpRateLimiter(window_seconds=60)
        for _ in range(2):
            assert rl.allow("a", 2) is True
        assert rl.allow("a", 2) is False
        # b still has full quota
        assert rl.allow("b", 2) is True

    def test_window_expiry(self) -> None:
        from api_server.ratelimit import IpRateLimiter
        base = [1_000_000.0]
        rl = IpRateLimiter(window_seconds=10, time_func=lambda: base[0])
        for _ in range(2):
            assert rl.allow("k", 2) is True
        assert rl.allow("k", 2) is False
        # Advance beyond the window
        base[0] += 11
        assert rl.allow("k", 2) is True
