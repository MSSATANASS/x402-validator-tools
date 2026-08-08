"""Endpoint tests for auth routes.

JSON-mode tests (no DATABASE_URL) run anywhere: auth routes must degrade
to 503. DB-gated tests need TEST_DATABASE_URL (Neon) and exercise the
real flows.
"""

from __future__ import annotations

import importlib
import os
import re
import secrets
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)


@pytest.fixture
def json_client(tmp_path, monkeypatch):
    """App in JSON-keystore mode: auth routes must answer 503."""
    monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import api_server.keystore  # noqa: F401
    import api_server.app  # noqa: F401
    importlib.reload(sys.modules["api_server.keystore"])
    app_mod = importlib.reload(sys.modules["api_server.app"])
    from api_server import auth as auth_mod, ratelimit as rl_mod
    auth_mod._user_stores.clear()
    rl_mod.reset_limiter()
    return TestClient(app_mod.app)


class TestJsonModeDegradation:
    def test_signup_get_503(self, json_client):
        assert json_client.get("/signup").status_code == 503

    def test_signup_post_503(self, json_client):
        r = json_client.post(
            "/signup", data={"email": "a@b.co", "password": "password123"}
        )
        assert r.status_code == 503

    def test_login_get_503(self, json_client):
        assert json_client.get("/login").status_code == 503

    def test_logout_without_db_still_works(self, json_client):
        r = json_client.post("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


# ---------------------------------------------------------------------------
# DB-gated flows (Neon)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_client():
    """App wired to the Neon test DB (one pool for the whole module).

    Rate limits are raised for the functional tests: all requests share the
    'testclient' IP, so the anti-abuse limiter (exercised separately in
    test_signup_rate_limited) would otherwise starve the signup flow.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    saved_signup_limit = os.environ.get("SIGNUP_DAILY_LIMIT")
    saved_login_limit = os.environ.get("LOGIN_DAILY_LIMIT")
    os.environ["SIGNUP_DAILY_LIMIT"] = "100000"
    os.environ["LOGIN_DAILY_LIMIT"] = "100000"
    import api_server.keystore  # noqa: F401
    import api_server.app  # noqa: F401
    importlib.reload(sys.modules["api_server.keystore"])
    app_mod = importlib.reload(sys.modules["api_server.app"])
    from api_server import auth as auth_mod, ratelimit as rl_mod
    auth_mod._user_stores.clear()
    rl_mod.reset_limiter()
    yield TestClient(app_mod.app)
    auth_mod._user_stores.clear()
    rl_mod.reset_limiter()
    if saved is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = saved
    for name, prev in (("SIGNUP_DAILY_LIMIT", saved_signup_limit),
                       ("LOGIN_DAILY_LIMIT", saved_login_limit)):
        if prev is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prev
    importlib.reload(sys.modules["api_server.keystore"])


def _signup(client, email=None, password="password123"):
    email = email or f"u-{secrets.token_hex(6)}@example.com"
    r = client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )
    return email, r


@needs_db
class TestSignupLoginLogout:
    def test_signup_creates_session_and_redirects(self, db_client):
        db_client.cookies.clear()
        email, r = _signup(db_client)
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        assert "x402_session" in db_client.cookies
        # (dashboard content checks live in TestDashboard — Task 5)
        assert email  # used by the flow

    def test_signup_validates_email(self, db_client):
        db_client.cookies.clear()
        r = db_client.post(
            "/signup", data={"email": "not-an-email", "password": "password123"}
        )
        assert r.status_code == 400
        assert "valid email" in r.text

    def test_signup_validates_password(self, db_client):
        db_client.cookies.clear()
        r = db_client.post(
            "/signup",
            data={"email": f"u-{secrets.token_hex(4)}@example.com",
                  "password": "short"},
        )
        assert r.status_code == 400
        assert "8" in r.text

    def test_signup_duplicate_email_409(self, db_client):
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        db_client.cookies.clear()
        r = db_client.post(
            "/signup", data={"email": email.upper(), "password": "password123"}
        )
        assert r.status_code == 409
        assert "already exists" in r.text

    def test_login_wrong_password_generic_error(self, db_client):
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        db_client.cookies.clear()
        r = db_client.post(
            "/login", data={"email": email, "password": "wrong-password"}
        )
        assert r.status_code == 401
        assert "Invalid email or password" in r.text
        assert "x402_session" not in db_client.cookies

    def test_login_unknown_email_same_generic_error(self, db_client):
        db_client.cookies.clear()
        r = db_client.post(
            "/login",
            data={"email": "ghost@example.com", "password": "password123"},
        )
        assert r.status_code == 401
        assert "Invalid email or password" in r.text

    def test_login_success_redirects_to_dashboard(self, db_client):
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        db_client.cookies.clear()
        r = db_client.post(
            "/login", data={"email": email, "password": "password123"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        assert "x402_session" in db_client.cookies

    def test_logout_redirects_home(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.post("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        # (revocation-vs-dashboard check lives in TestDashboard — Task 5)

    def test_signup_rate_limited(self, db_client, monkeypatch):
        from api_server import ratelimit
        ratelimit.reset_limiter()
        monkeypatch.setenv("SIGNUP_DAILY_LIMIT", "2")
        db_client.cookies.clear()
        _signup(db_client); db_client.cookies.clear()
        _signup(db_client); db_client.cookies.clear()
        _, r = _signup(db_client)
        assert r.status_code == 429
        ratelimit.reset_limiter()


KEY_BOX_RE = re.compile(r'id="keyBox">([^<]+)</div>')


class TestDashboardJsonMode:
    def test_dashboard_redirects_to_login_without_session(self, json_client):
        r = json_client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"


@needs_db
class TestDashboard:
    def test_dashboard_requires_session(self, db_client):
        db_client.cookies.clear()
        r = db_client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_dashboard_shows_email(self, db_client):
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        dash = db_client.get("/dashboard")
        assert dash.status_code == 200
        assert email in dash.text

    def test_logout_revokes_session(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        db_client.post("/logout", follow_redirects=False)
        dash = db_client.get("/dashboard", follow_redirects=False)
        assert dash.status_code == 303
        assert dash.headers["location"] == "/login"

    def test_create_key_shown_once_and_listed_masked(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.post("/dashboard/keys")
        assert r.status_code == 200
        m = KEY_BOX_RE.search(r.text)
        assert m, "key box missing from the new-key page"
        token = m.group(1).strip()
        assert len(token) >= 40
        dash = db_client.get("/dashboard")
        assert token not in dash.text, "raw token leaked into the dashboard"
        from api_server import auth as auth_mod
        assert auth_mod.kid_for_token(token) in dash.text

    def test_new_key_works_for_validate_gate(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.post("/dashboard/keys")
        token = KEY_BOX_RE.search(r.text).group(1).strip()
        from api_server.keystore import get_store
        assert get_store().get(token) == "free"  # signup plan

    def test_revoke_own_key_only(self, db_client):
        from api_server import auth as auth_mod
        from api_server.keystore import get_store
        db_client.cookies.clear()
        email1, _ = _signup(db_client)
        r = db_client.post("/dashboard/keys")
        token = KEY_BOX_RE.search(r.text).group(1).strip()
        kid = auth_mod.kid_for_token(token)

        # a different user cannot revoke it
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.post("/dashboard/keys/revoke", data={"kid": kid})
        assert r.status_code == 404
        assert get_store().get(token) == "free"  # still alive

        # the owner can
        db_client.cookies.clear()
        db_client.post(
            "/login", data={"email": email1, "password": "password123"}
        )
        r = db_client.post(
            "/dashboard/keys/revoke", data={"kid": kid},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert get_store().get(token) is None
        assert kid not in db_client.get("/dashboard").text


class TestStripeCheckoutExtensions:
    def test_create_checkout_passes_user_link_params(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
        import api_server.stripe_integration as si
        captured = {}

        class FakeSession:
            url = "https://checkout.stripe.com/mock"

            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return FakeSession()

        class FakeCheckout:
            Session = FakeSession

        class FakeStripe:
            checkout = FakeCheckout()

        monkeypatch.setattr(si, "_get_stripe", lambda: FakeStripe())
        url = si.create_checkout_session(
            "pro", success_url="https://x/success", cancel_url="https://x/cancel",
            client_reference_id="user:7", customer_email="a@b.co",
        )
        assert url == "https://checkout.stripe.com/mock"
        assert captured["client_reference_id"] == "user:7"
        assert captured["customer_email"] == "a@b.co"
        assert "customer" not in captured  # never both email and customer

    def test_retrieve_session_includes_link_fields(self, monkeypatch):
        import api_server.stripe_integration as si

        class Sess:
            id = "cs_1"
            customer = "cus_1"
            amount_total = 900
            subscription = None
            mode = "subscription"
            metadata = {"plan_id": "pro"}
            client_reference_id = "user:3"
            customer_email = "a@b.co"

        class FakeSession:
            @staticmethod
            def retrieve(sid):
                return Sess()

        class FakeCheckout:
            Session = FakeSession

        class FakeStripe:
            checkout = FakeCheckout()

        monkeypatch.setattr(si, "_get_stripe", lambda: FakeStripe())
        d = si.retrieve_session("cs_1")
        assert d["client_reference_id"] == "user:3"
        assert d["customer_email"] == "a@b.co"


@needs_db
class TestUpgradeAndWebhook:
    def test_upgrade_rejects_bad_plans(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.get("/dashboard/upgrade?plan_id=free",
                          follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        r = db_client.get("/dashboard/upgrade?plan_id=nope",
                          follow_redirects=False)
        assert r.status_code == 400

    def test_upgrade_without_stripe_503(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        with patch("api_server.auth_pages.stripe_integration"
                   ".create_checkout_session", return_value=None):
            r = db_client.get("/dashboard/upgrade?plan_id=pro")
        assert r.status_code == 503

    def test_upgrade_redirects_with_client_reference(self, db_client):
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        captured = {}

        def fake_create(plan_id, *, success_url, cancel_url, **kw):
            captured.update(plan_id=plan_id, success_url=success_url,
                            cancel_url=cancel_url, **kw)
            return "https://checkout.stripe.com/mock"

        with patch("api_server.auth_pages.stripe_integration"
                   ".create_checkout_session", fake_create):
            r = db_client.get("/dashboard/upgrade?plan_id=pro",
                              follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "https://checkout.stripe.com/mock"
        assert captured["plan_id"] == "pro"
        assert captured["client_reference_id"].startswith("user:")
        assert captured["customer_email"] == email
        assert captured.get("customer") is None

    def test_webhook_links_purchase_to_user(self, db_client):
        from api_server import auth as auth_mod
        from api_server.keystore import get_store
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        uid = auth_mod.get_user_store().authenticate(email, "password123")
        session_id = f"cs_user_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_linked",
                   "amount_total": None, "subscription": "sub_x",
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": f"user:{uid}",
                   "customer_email": email}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            r = db_client.post("/stripe-webhook", content=b"{}",
                               headers={"stripe-signature": "ok"})
        assert r.status_code == 200
        body = r.json()
        assert body["minted"] is True
        assert body["user_id"] == uid
        u = auth_mod.get_user_store().get_user(uid)
        assert u["plan_id"] == "pro"
        assert u["stripe_customer_id"] == "cus_linked"
        claim = get_store().claim_by_session(session_id)
        assert claim is not None
        assert auth_mod.get_user_store().key_owner(claim["api_key"]) == uid
        # the key shows up on the dashboard (session still valid)
        dash = db_client.get("/dashboard")
        assert dash.status_code == 200
        assert auth_mod.kid_for_token(claim["api_key"]) in dash.text

    def test_webhook_anonymous_flow_unchanged(self, db_client):
        db_client.cookies.clear()
        session_id = f"cs_anon_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_anon",
                   "amount_total": None, "subscription": None,
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": None, "customer_email": None}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            r = db_client.post("/stripe-webhook", content=b"{}",
                               headers={"stripe-signature": "ok"})
        assert r.status_code == 200
        body = r.json()
        assert body["minted"] is True
        assert "user_id" not in body  # anonymous: no linking

    def test_webhook_bad_user_ref_falls_back_to_anonymous(self, db_client):
        db_client.cookies.clear()
        session_id = f"cs_badref_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_bad",
                   "amount_total": None, "subscription": None,
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": "user:99999999",  # no such user
                   "customer_email": None}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            r = db_client.post("/stripe-webhook", content=b"{}",
                               headers={"stripe-signature": "ok"})
        assert r.status_code == 200
        body = r.json()
        assert body["minted"] is True          # purchase never lost
        assert "user_id" not in body


@needs_db
class TestLandingNavAndSuccess:
    def test_landing_shows_auth_links_logged_out(self, db_client):
        db_client.cookies.clear()
        r = db_client.get("/")
        assert 'href="/login"' in r.text
        assert 'href="/signup"' in r.text
        assert "My dashboard" not in r.text

    def test_landing_shows_dashboard_link_logged_in(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.get("/")
        assert 'href="/dashboard"' in r.text

    def test_success_page_note_for_owner(self, db_client):
        from api_server import auth as auth_mod
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        uid = auth_mod.get_user_store().authenticate(email, "password123")
        session_id = f"cs_note_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_note",
                   "amount_total": None, "subscription": None,
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": f"user:{uid}",
                   "customer_email": email}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            db_client.post("/stripe-webhook", content=b"{}",
                           headers={"stripe-signature": "ok"})
        r = db_client.get(f"/success?session_id={session_id}")
        assert r.status_code == 200
        # the note's anchor (the warn paragraph also mentions "your
        # dashboard" in plain text, so assert the exact link)
        assert '<a href="/dashboard">your dashboard</a>' in r.text

    def test_success_page_no_note_for_strangers(self, db_client):
        db_client.cookies.clear()
        # logged out visitor: no note even with a valid claim
        session_id = f"cs_note2_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_note2",
                   "amount_total": None, "subscription": None,
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": None, "customer_email": None}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            db_client.post("/stripe-webhook", content=b"{}",
                           headers={"stripe-signature": "ok"})
        r = db_client.get(f"/success?session_id={session_id}")
        assert r.status_code == 200
        assert '<a href="/dashboard">your dashboard</a>' not in r.text
