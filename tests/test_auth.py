"""Tests for api_server.auth (user accounts).

Layout: TestPrimitives runs anywhere (no DB). TestUserStoreIntegration
(Task 3) runs only with TEST_DATABASE_URL.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sys
import types

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

    def test_password_none_and_whitespace_email(self):
        assert not auth.is_valid_password(None)  # type: ignore[arg-type]
        assert auth.normalize_email(None) == ""  # type: ignore[arg-type]
        assert not auth.is_valid_email("")

    def test_token_hash_length_and_constants(self):
        assert auth.SESSION_COOKIE == "x402_session"
        assert auth.SESSION_TTL_DAYS == 30
        assert auth.PASSWORD_MIN_LEN == 8
        h = auth._token_hash("abc")
        assert len(h) == 64
        assert h == hashlib.sha256(b"abc").hexdigest()

    def test_get_user_store_none_without_postgres(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "keys.json"))
        import api_server.keystore as km

        km.reset_default_store()
        assert auth.get_user_store() is None

    def test_get_user_store_none_when_pool_missing(self, monkeypatch):
        class FakeStore:
            backend = "postgres"
            # no .pool attribute

        monkeypatch.setattr(auth, "get_store", lambda: FakeStore())
        auth._user_stores.clear()
        assert auth.get_user_store() is None

    def test_auth_schema_statements_are_sql(self):
        assert len(auth.AUTH_SCHEMA_STATEMENTS) >= 4
        joined = " ".join(auth.AUTH_SCHEMA_STATEMENTS).upper()
        assert "X402_USERS" in joined
        assert "X402_SESSIONS" in joined


# ---------------------------------------------------------------------------
# UserStore with a hermetic fake pool (no Neon / TEST_DATABASE_URL)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, row=None, rows=None, rowcount: int = 0):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    """Minimal connection: routes SQL by substring to canned results."""

    def __init__(self, router: dict):
        # router: list of (predicate, cursor_or_callable)
        self._router = router
        self.executes: list[tuple[str, object]] = []
        self._tx = False

    def execute(self, sql: str, params=None):
        self.executes.append((sql, params))
        for pred, handler in self._router:
            if pred(sql, params):
                if callable(handler):
                    return handler(sql, params)
                return handler
        return _FakeCursor()

    def transaction(self):
        # Context manager for `with conn.transaction()`
        class _Tx:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

        return _Tx()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakePool:
    def __init__(self, router: dict | list | None = None):
        # Accept list of (pred, handler) pairs
        self._router = list(router or [])
        self.conns: list[_FakeConn] = []

    def connection(self):
        conn = _FakeConn(self._router)
        self.conns.append(conn)
        return conn


def _sql_contains(*needles: str):
    needles_u = [n.upper() for n in needles]

    def pred(sql: str, _params) -> bool:
        s = sql.upper()
        return all(n in s for n in needles_u)

    return pred


class TestUserStoreMocked:
    """Cover UserStore branches without a real database."""

    def test_init_runs_schema_statements(self):
        pool = _FakePool()
        store = auth.UserStore(pool)
        assert store._pool is pool
        # __init__ opens one connection and runs every AUTH_SCHEMA statement
        assert len(pool.conns) == 1
        assert len(pool.conns[0].executes) == len(auth.AUTH_SCHEMA_STATEMENTS)

    def test_create_user_returns_id(self):
        pool = _FakePool([
            (_sql_contains("INSERT INTO X402_USERS"), _FakeCursor(row=(42,))),
        ])
        store = auth.UserStore(pool)
        # Reset executes from schema init so we only assert create
        pool.conns.clear()
        uid = store.create_user("  New@Example.COM ", "password123")
        assert uid == 42
        # Email was normalized
        sql, params = pool.conns[-1].executes[0]
        assert params[0] == "new@example.com"
        assert params[1].startswith("$argon2")

    def test_create_user_duplicate_raises(self, monkeypatch):
        class UniqueViolation(Exception):
            pass

        # Inject the exception type that UserStore imports from psycopg.errors
        fake_mod = types.ModuleType("psycopg.errors")
        fake_mod.UniqueViolation = UniqueViolation  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "psycopg.errors", fake_mod)

        def boom(_sql, _params):
            raise UniqueViolation()

        pool = _FakePool([
            (_sql_contains("INSERT INTO X402_USERS"), boom),
        ])
        store = auth.UserStore(pool)
        with pytest.raises(auth.DuplicateEmail):
            store.create_user("dup@example.com", "password123")

    def test_get_user_found_and_missing(self):
        from datetime import datetime, timezone

        created = datetime(2026, 8, 1, tzinfo=timezone.utc)
        pool = _FakePool([
            (
                _sql_contains("FROM X402_USERS WHERE ID"),
                _FakeCursor(row=(7, "a@b.co", "pro", "cus_1", created)),
            ),
        ])
        store = auth.UserStore(pool)
        u = store.get_user(7)
        assert u == {
            "id": 7,
            "email": "a@b.co",
            "plan_id": "pro",
            "stripe_customer_id": "cus_1",
            "created_at": created,
        }

        pool2 = _FakePool([
            (_sql_contains("FROM X402_USERS WHERE ID"), _FakeCursor(row=None)),
        ])
        store2 = auth.UserStore(pool2)
        assert store2.get_user(999) is None

    def test_authenticate_success_wrong_and_missing(self):
        h = auth.hash_password("correct-horse")
        pool = _FakePool([
            (
                _sql_contains("PASSWORD_HASH"),
                _FakeCursor(row=(11, h)),
            ),
        ])
        store = auth.UserStore(pool)
        assert store.authenticate("user@ex.com", "correct-horse") == 11
        assert store.authenticate("user@ex.com", "wrong-password") is None

        # Missing email: burns dummy hash, returns None
        pool_miss = _FakePool([
            (_sql_contains("PASSWORD_HASH"), _FakeCursor(row=None)),
        ])
        store_miss = auth.UserStore(pool_miss)
        assert store_miss.authenticate("nobody@ex.com", "whatever") is None
        # Second miss reuses global _dummy_hash
        assert store_miss.authenticate("nobody@ex.com", "whatever") is None

    def test_set_plan(self):
        pool = _FakePool()
        store = auth.UserStore(pool)
        pool.conns.clear()
        store.set_plan(3, "enterprise", "cus_x")
        sql, params = pool.conns[-1].executes[0]
        assert "UPDATE X402_USERS" in sql.upper()
        assert params == ("enterprise", "cus_x", 3)

    def test_create_and_revoke_session(self):
        pool = _FakePool()
        store = auth.UserStore(pool)
        pool.conns.clear()
        token = store.create_session(5)
        assert isinstance(token, str) and len(token) > 20
        # DELETE expired + INSERT
        assert len(pool.conns[-1].executes) == 2
        assert "DELETE FROM X402_SESSIONS" in pool.conns[-1].executes[0][0].upper()
        assert "INSERT INTO X402_SESSIONS" in pool.conns[-1].executes[1][0].upper()

        pool.conns.clear()
        store.revoke_session(token)
        assert "DELETE FROM X402_SESSIONS" in pool.conns[-1].executes[0][0].upper()
        store.revoke_session("")  # no-op, no new execute beyond empty path

    def test_get_session_user_empty_token(self):
        pool = _FakePool()
        store = auth.UserStore(pool)
        assert store.get_session_user("") is None
        assert store.get_session_user(None) is None  # type: ignore[arg-type]

    def test_get_session_user_valid(self):
        from datetime import datetime, timedelta, timezone

        future = datetime.now(timezone.utc) + timedelta(days=1)
        pool = _FakePool([
            (
                _sql_contains("JOIN X402_USERS"),
                _FakeCursor(row=(9, "s@ex.com", "free", future)),
            ),
        ])
        store = auth.UserStore(pool)
        user = store.get_session_user("tok-valid")
        assert user == {"id": 9, "email": "s@ex.com", "plan_id": "free"}

    def test_get_session_user_missing(self):
        pool = _FakePool([
            (_sql_contains("JOIN X402_USERS"), _FakeCursor(row=None)),
        ])
        store = auth.UserStore(pool)
        assert store.get_session_user("ghost") is None

    def test_get_session_user_expired_naive_and_aware(self):
        from datetime import datetime, timedelta, timezone

        # Naive expired timestamp (forces tzinfo branch)
        past_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        deletes: list = []

        def expired_handler(sql, params):
            if "DELETE FROM" in sql.upper():
                deletes.append(params)
                return _FakeCursor()
            return _FakeCursor(row=(1, "e@x.com", "free", past_naive))

        pool = _FakePool([
            (lambda s, p: "X402_SESSIONS" in s.upper(), expired_handler),
        ])
        store = auth.UserStore(pool)
        assert store.get_session_user("expired-tok") is None
        assert deletes  # session row deleted

        past_aware = datetime.now(timezone.utc) - timedelta(days=2)
        pool2 = _FakePool([
            (
                _sql_contains("JOIN X402_USERS"),
                _FakeCursor(row=(2, "e2@x.com", "free", past_aware)),
            ),
            (_sql_contains("DELETE FROM X402_SESSIONS"), _FakeCursor()),
        ])
        store2 = auth.UserStore(pool2)
        assert store2.get_session_user("expired-2") is None

    def test_issue_and_list_keys(self):
        from datetime import datetime, timezone

        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # issue_key uses INSERT; list_keys uses SELECT
        token_holder: dict = {}

        def list_handler(sql, params):
            return _FakeCursor(rows=[(token_holder["t"], "pro", created)])

        pool = _FakePool([
            (_sql_contains("INSERT INTO X402_API_KEYS"), _FakeCursor()),
            (_sql_contains("SELECT TOKEN, PLAN_ID"), list_handler),
        ])
        store = auth.UserStore(pool)
        pool.conns.clear()
        tok = store.issue_key(1, "pro")
        token_holder["t"] = tok
        keys = store.list_keys(1)
        assert len(keys) == 1
        assert keys[0]["plan_id"] == "pro"
        assert keys[0]["token"] == tok
        assert keys[0]["kid"] == auth.kid_for_token(tok)
        assert keys[0]["created_at"] == created

    def test_revoke_key_by_kid_found_and_miss(self):
        real = secrets.token_urlsafe(16)
        kid = auth.kid_for_token(real)

        def select_handler(sql, params):
            return _FakeCursor(rows=[(real,), ("other-token",)])

        pool = _FakePool([
            (_sql_contains("SELECT TOKEN FROM X402_API_KEYS"), select_handler),
            (
                _sql_contains("DELETE FROM X402_API_KEYS"),
                _FakeCursor(rowcount=1),
            ),
        ])
        store = auth.UserStore(pool)
        assert store.revoke_key_by_kid(1, kid) is True
        assert store.revoke_key_by_kid(1, "deadbeefdead") is False

    def test_revoke_key_rowcount_zero(self):
        real = secrets.token_urlsafe(8)
        kid = auth.kid_for_token(real)
        pool = _FakePool([
            (
                _sql_contains("SELECT TOKEN FROM X402_API_KEYS"),
                _FakeCursor(rows=[(real,)]),
            ),
            (
                _sql_contains("DELETE FROM X402_API_KEYS"),
                _FakeCursor(rowcount=0),
            ),
        ])
        store = auth.UserStore(pool)
        assert store.revoke_key_by_kid(1, kid) is False

    def test_key_owner(self):
        pool = _FakePool([
            (
                _sql_contains("SELECT USER_ID FROM X402_API_KEYS"),
                _FakeCursor(row=(55,)),
            ),
        ])
        store = auth.UserStore(pool)
        assert store.key_owner("tok") == 55

        pool2 = _FakePool([
            (
                _sql_contains("SELECT USER_ID"),
                _FakeCursor(row=(None,)),
            ),
        ])
        assert auth.UserStore(pool2).key_owner("x") is None
        pool3 = _FakePool([
            (_sql_contains("SELECT USER_ID"), _FakeCursor(row=None)),
        ])
        assert auth.UserStore(pool3).key_owner("x") is None

    def test_link_purchase(self):
        pool = _FakePool()
        store = auth.UserStore(pool)
        pool.conns.clear()
        token = store.link_purchase(3, "pro", "cus_99", "cs_sess_1")
        assert isinstance(token, str) and len(token) > 10
        conn = pool.conns[-1]
        # 3 statements: insert key, insert claim, update user
        assert len(conn.executes) == 3
        assert "INSERT INTO X402_API_KEYS" in conn.executes[0][0].upper()
        assert "INSERT INTO X402_CLAIMS" in conn.executes[1][0].upper()
        assert "UPDATE X402_USERS" in conn.executes[2][0].upper()
        assert conn.executes[0][1][0] == token
        assert conn.executes[1][1][0] == "cs_sess_1"

    def test_get_user_store_caches_per_store(self, monkeypatch):
        pool = _FakePool()

        class FakeStore:
            backend = "postgres"

            def __init__(self):
                self.pool = pool

        fake = FakeStore()
        monkeypatch.setattr(auth, "get_store", lambda: fake)
        auth._user_stores.clear()
        a = auth.get_user_store()
        b = auth.get_user_store()
        assert a is b
        assert isinstance(a, auth.UserStore)


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
