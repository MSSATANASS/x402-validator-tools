"""Tests for api_server.auth (user accounts).

Layout: TestPrimitives runs anywhere (no DB). TestUserStoreIntegration
(Task 3) runs only with TEST_DATABASE_URL.
"""

from __future__ import annotations

import hashlib
import os
import secrets

import pytest

from api_server import auth


class TestPrimitives:
    def test_normalize_email(self):
        assert auth.normalize_email("  Foo@Example.COM ") == "foo@example.com"
        assert auth.normalize_email("") == ""

    def test_is_valid_email(self):
        assert auth.is_valid_email("a@b.co")
        assert auth.is_valid_email("user.name+tag@sub.example.org")
        assert not auth.is_valid_email("no-at-sign")
        assert not auth.is_valid_email("a@b")          # no dot in domain
        assert not auth.is_valid_email("a b@c.de")     # space
        assert not auth.is_valid_email("x" * 400)      # too long

    def test_is_valid_password_bounds(self):
        assert not auth.is_valid_password("7chars!")       # 7 chars
        assert auth.is_valid_password("8chars!!")          # 8 chars
        assert auth.is_valid_password("x" * 200)
        assert not auth.is_valid_password("x" * 201)
        assert not auth.is_valid_password("")

    def test_password_hash_roundtrip(self):
        h = auth.hash_password("correct horse battery")
        assert h != "correct horse battery"
        assert h.startswith("$argon2")
        assert auth.verify_password(h, "correct horse battery") is True
        assert auth.verify_password(h, "wrong password") is False

    def test_verify_password_garbage_hash_is_false(self):
        assert auth.verify_password("not-a-real-hash", "whatever") is False

    def test_kid_for_token(self):
        kid = auth.kid_for_token("tok-abc")
        assert len(kid) == auth.KID_LEN
        assert all(c in "0123456789abcdef" for c in kid)
        assert kid == auth.kid_for_token("tok-abc")      # deterministic
        assert kid != auth.kid_for_token("tok-xyz")


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (point it at the Neon test DB)",
)


def _unique_email() -> str:
    return f"u-{secrets.token_hex(6)}@example.com"


@pytest.fixture(scope="module")
def stores():
    """Shared DBKeyStore + UserStore on the Neon test DB (one pool)."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    from api_server.dbkeystore import DBKeyStore

    db = DBKeyStore(TEST_DATABASE_URL)
    us = auth.UserStore(db.pool)
    yield db, us
    db.close()


@needs_db
class TestUserStoreIntegration:
    def test_create_get_authenticate(self, stores):
        _, us = stores
        email = _unique_email()
        uid = us.create_user(email, "password123")
        u = us.get_user(uid)
        assert u is not None
        assert u["email"] == email
        assert u["plan_id"] == "free"
        assert u["stripe_customer_id"] is None
        assert us.authenticate(email, "password123") == uid
        assert us.authenticate(email, "wrong-password") is None
        assert us.authenticate("missing@example.com", "password123") is None

    def test_duplicate_email_raises(self, stores):
        _, us = stores
        email = _unique_email()
        us.create_user(email, "password123")
        with pytest.raises(auth.DuplicateEmail):
            us.create_user(email.upper(), "password456")  # normalized dup

    def test_session_lifecycle(self, stores):
        _, us = stores
        uid = us.create_user(_unique_email(), "password123")
        token = us.create_session(uid)
        user = us.get_session_user(token)
        assert user is not None
        assert user["id"] == uid
        assert user["plan_id"] == "free"
        us.revoke_session(token)
        assert us.get_session_user(token) is None
        assert us.get_session_user("") is None
        assert us.get_session_user("never-a-session") is None

    def test_expired_session_rejected(self, stores):
        db, us = stores
        uid = us.create_user(_unique_email(), "password123")
        token = us.create_session(uid)
        with db.pool.connection() as conn:
            conn.execute(
                "UPDATE x402_sessions SET expires_at = now() - interval '1 day' "
                "WHERE token_hash = %s",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )
        assert us.get_session_user(token) is None

    def test_key_issue_list_revoke(self, stores):
        _, us = stores
        uid = us.create_user(_unique_email(), "password123")
        token = us.issue_key(uid, "pro")
        keys = us.list_keys(uid)
        assert len(keys) == 1
        assert keys[0]["kid"] == auth.kid_for_token(token)
        assert keys[0]["plan_id"] == "pro"
        # another user cannot revoke it
        uid2 = us.create_user(_unique_email(), "password123")
        assert us.revoke_key_by_kid(uid2, keys[0]["kid"]) is False
        # owner can
        assert us.revoke_key_by_kid(uid, keys[0]["kid"]) is True
        assert us.list_keys(uid) == []
        assert us.revoke_key_by_kid(uid, keys[0]["kid"]) is False

    def test_key_owner(self, stores):
        db, us = stores
        uid = us.create_user(_unique_email(), "password123")
        token = us.issue_key(uid, "free")
        assert us.key_owner(token) == uid
        anon = db.issue("free")
        try:
            assert us.key_owner(anon) is None  # anonymous key: no owner
        finally:
            db.revoke(anon)
        assert us.key_owner("never-existed") is None
        us.revoke_key_by_kid(uid, auth.kid_for_token(token))

    def test_link_purchase(self, stores):
        db, us = stores
        uid = us.create_user(_unique_email(), "password123")
        session_id = f"cs_test_{secrets.token_hex(4)}"
        token = us.link_purchase(uid, "pro", "cus_link_test", session_id)
        try:
            u = us.get_user(uid)
            assert u["plan_id"] == "pro"
            assert u["stripe_customer_id"] == "cus_link_test"
            claim = db.claim_by_session(session_id)
            assert claim is not None and claim["api_key"] == token
            assert us.key_owner(token) == uid
            assert db.get(token) == "pro"  # key is live for /validate
        finally:
            db.revoke(token)

    def test_set_plan_keeps_customer_when_none(self, stores):
        _, us = stores
        uid = us.create_user(_unique_email(), "password123")
        us.set_plan(uid, "pro", stripe_customer_id="cus_keep")
        us.set_plan(uid, "enterprise", stripe_customer_id=None)
        u = us.get_user(uid)
        assert u["plan_id"] == "enterprise"
        assert u["stripe_customer_id"] == "cus_keep"  # COALESCE keeps it
