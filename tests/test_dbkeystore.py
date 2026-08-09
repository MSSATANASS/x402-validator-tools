"""Tests for keystore backend dispatch + the PostgreSQL keystore.

Layout:

- TestBackendDispatch / TestJsonCompat run anywhere (no database needed).
- TestDBKeyStoreIntegration runs only when ``TEST_DATABASE_URL`` points at a
  reachable PostgreSQL / PolarDB instance (e.g. a dockerized
  ``postgres:16-alpine`` or the always-free PolarDB cluster). Otherwise the
  tests are skipped — never faked.

Run integration with:
    TEST_DATABASE_URL=postgresql://x402:x402@localhost:5432/x402 pytest -k DBKeyStore
"""

from __future__ import annotations

import os
import sys
import types

import pytest

import api_server.keystore as keystore_mod


# ---------------------------------------------------------------------------
# Backend dispatch (no DB required)
# ---------------------------------------------------------------------------


class TestBackendDispatch:
    def setup_method(self):
        self._saved = keystore_mod._store

    def teardown_method(self):
        keystore_mod._store = self._saved

    def test_defaults_to_json_store(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
        keystore_mod._store = None
        store = keystore_mod.get_store()
        assert type(store) is keystore_mod.KeyStore
        assert store.backend == "json"

    def test_picks_db_store_when_url_set(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

        class FakeDBStore:
            def __init__(self, url):
                self.url = url

        fake_mod = types.ModuleType("api_server.dbkeystore")
        fake_mod.DBKeyStore = FakeDBStore
        monkeypatch.setitem(sys.modules, "api_server.dbkeystore", fake_mod)

        keystore_mod._store = None
        store = keystore_mod.get_store()
        assert isinstance(store, FakeDBStore)
        assert store.url == "postgresql://fake:fake@localhost/fake"

    def test_reset_store_forces_json(self, monkeypatch, tmp_path):
        """reset_store() must keep returning a JSON KeyStore (test contract)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
        store = keystore_mod.reset_store(tmp_path / "k.json")
        assert type(store) is keystore_mod.KeyStore
        assert keystore_mod.get_store() is store


class TestJsonCompat:
    """The JSON store must expose the new accounting API as no-ops."""

    def test_quota_and_usage_noops(self, tmp_path):
        store = keystore_mod.KeyStore(tmp_path / "k.json")
        key = store.issue("pro")
        assert store.usage_this_month(key) == 0
        assert store.quota_allows(key, "pro") is True
        assert store.quota_allows(key, "unknown-plan") is True
        # record_audit is a silent no-op
        assert store.record_audit(url="https://x", mode="standard") is None


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
    def __init__(self, router: list):
        self._router = router
        self.executes: list[tuple] = []

    def execute(self, sql, params=None):
        self.executes.append((sql, params))
        for pred, handler in self._router:
            if pred(sql, params):
                return handler(sql, params) if callable(handler) else handler
        return _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakePool:
    def __init__(self, router: list | None = None):
        self._router = list(router or [])
        self.closed = False
        self.conns: list[_FakeConn] = []

    def connection(self):
        conn = _FakeConn(self._router)
        self.conns.append(conn)
        return conn

    def close(self):
        self.closed = True


def _has(*needles: str):
    needles_u = [n.upper() for n in needles]

    def pred(sql: str, _params) -> bool:
        s = sql.upper()
        return all(n in s for n in needles_u)

    return pred


def _store_with(router: list | None = None):
    from api_server.dbkeystore import DBKeyStore

    store = object.__new__(DBKeyStore)
    store._pool = _FakePool(router)  # type: ignore[attr-defined]
    return store


class TestDBKeyStoreUnit:
    """Hermetic coverage for pure helpers + mock-backed methods (no Postgres)."""

    def test_iso_none_and_naive_datetime(self):
        from datetime import datetime, timezone

        from api_server.dbkeystore import _iso

        assert _iso(None) is None
        naive = datetime(2026, 8, 1, 12, 0, 0)
        out = _iso(naive)
        assert out is not None and out.endswith("+00:00")
        aware = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert _iso(aware) == aware.isoformat()
        assert _iso("already-a-string") == "already-a-string"

    def test_ensure_schema_runs_all_statements(self):
        from api_server.dbkeystore import SCHEMA_STATEMENTS, ensure_schema

        class FakeConn:
            def __init__(self):
                self.stmts: list[str] = []

            def execute(self, stmt):
                self.stmts.append(stmt)

        conn = FakeConn()
        ensure_schema(conn)
        assert len(conn.stmts) == len(SCHEMA_STATEMENTS)
        assert any("x402_api_keys" in s for s in conn.stmts)

    def test_init_requires_database_url(self, monkeypatch):
        from api_server.dbkeystore import DBKeyStore

        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            DBKeyStore("")

    def test_init_builds_pool_and_schema(self, monkeypatch):
        """__init__ path with ConnectionPool mocked (no real DB)."""
        import types

        from api_server import dbkeystore as dbm

        created = {}

        class FakePool:
            def __init__(self, conninfo, min_size=1, max_size=5, name="", open=True):
                created["conninfo"] = conninfo
                created["name"] = name
                self.schema_ran = False

            def connection(self):
                class C:
                    def execute(self_inner, stmt):
                        created.setdefault("stmts", []).append(stmt)

                    def __enter__(self_inner):
                        return self_inner

                    def __exit__(self_inner, *a):
                        return False

                return C()

            def close(self):
                pass

        fake_pool_mod = types.ModuleType("psycopg_pool")
        fake_pool_mod.ConnectionPool = FakePool  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "psycopg_pool", fake_pool_mod)

        store = dbm.DBKeyStore("postgresql://u:p@localhost/x402", min_size=2, max_size=4)
        assert created["conninfo"] == "postgresql://u:p@localhost/x402"
        assert created["name"] == "x402-keystore"
        assert len(created["stmts"]) == len(dbm.SCHEMA_STATEMENTS)
        assert store.backend == "postgres"
        assert store.pool is store._pool
        store.close()

    def test_init_reads_database_url_env(self, monkeypatch):
        import types

        from api_server import dbkeystore as dbm

        monkeypatch.setenv("DATABASE_URL", "postgresql://env/db")

        class FakePool:
            def __init__(self, conninfo, **kw):
                self.conninfo = conninfo

            def connection(self):
                class C:
                    def execute(self_inner, stmt):
                        pass

                    def __enter__(self_inner):
                        return self_inner

                    def __exit__(self_inner, *a):
                        return False

                return C()

        fake_pool_mod = types.ModuleType("psycopg_pool")
        fake_pool_mod.ConnectionPool = FakePool  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "psycopg_pool", fake_pool_mod)

        store = dbm.DBKeyStore(None)
        assert store._pool.conninfo == "postgresql://env/db"

    def test_get_found_contains_and_getitem(self):
        store = _store_with([
            (
                _has("SELECT PLAN_ID"),
                _FakeCursor(row=("pro",)),
            ),
        ])
        assert store.get("tok") == "pro"
        assert "tok" in store
        assert store["tok"] == "pro"

    def test_get_missing_and_getitem_raises(self):
        store = _store_with([
            (_has("SELECT PLAN_ID"), _FakeCursor(row=None)),
        ])
        assert store.get("nope") is None
        assert "nope" not in store
        with pytest.raises(KeyError):
            _ = store["nope"]

    def test_all_keys(self):
        store = _store_with([
            (
                _has("SELECT TOKEN, PLAN_ID"),
                _FakeCursor(rows=[("a", "free"), ("b", "pro")]),
            ),
        ])
        assert store.all() == {"a": "free", "b": "pro"}

    def test_issue_without_and_with_session(self):
        store = _store_with()
        tok = store.issue("pro", customer_id="cus_1")
        assert isinstance(tok, str) and len(tok) > 10
        # Only INSERT into api_keys
        conn = store._pool.conns[-1]
        assert len(conn.executes) == 1
        assert "INSERT INTO X402_API_KEYS" in conn.executes[0][0].upper()
        assert conn.executes[0][1][1] == "pro"

        store2 = _store_with()
        tok2 = store2.issue("enterprise", customer_id="cus_2", session_id="cs_abc")
        conn2 = store2._pool.conns[-1]
        assert len(conn2.executes) == 2
        assert "INSERT INTO X402_CLAIMS" in conn2.executes[1][0].upper()
        assert conn2.executes[1][1][0] == "cs_abc"
        assert conn2.executes[1][1][2] == tok2

    def test_revoke(self):
        store = _store_with([
            (_has("DELETE FROM X402_API_KEYS"), _FakeCursor(rowcount=1)),
        ])
        assert store.revoke("alive") is True

        store2 = _store_with([
            (_has("DELETE FROM X402_API_KEYS"), _FakeCursor(rowcount=0)),
        ])
        assert store2.revoke("ghost") is False

    def test_claim_by_session(self):
        from datetime import datetime, timezone

        issued = datetime(2026, 7, 1, tzinfo=timezone.utc)
        store = _store_with([
            (
                _has("FROM X402_CLAIMS WHERE SESSION_ID"),
                _FakeCursor(row=("pro", "tok-1", "cus", issued, None)),
            ),
        ])
        claim = store.claim_by_session("cs_1")
        assert claim is not None
        assert claim["plan_id"] == "pro"
        assert claim["api_key"] == "tok-1"
        assert claim["claimed_at"] is None
        assert claim["issued_at"] is not None

        assert store.claim_by_session("") is None
        assert store.claim_by_session(None) is None

        store2 = _store_with([
            (_has("FROM X402_CLAIMS"), _FakeCursor(row=None)),
        ])
        assert store2.claim_by_session("missing") is None

    def test_mark_claimed(self):
        store = _store_with([
            (_has("UPDATE X402_CLAIMS"), _FakeCursor(rowcount=1)),
        ])
        assert store.mark_claimed("cs") is True
        store2 = _store_with([
            (_has("UPDATE X402_CLAIMS"), _FakeCursor(rowcount=0)),
        ])
        assert store2.mark_claimed("cs") is False

    def test_claims_all(self):
        from datetime import datetime, timezone

        issued = datetime(2026, 6, 1, tzinfo=timezone.utc)
        claimed = datetime(2026, 6, 2, tzinfo=timezone.utc)
        store = _store_with([
            (
                _has("FROM X402_CLAIMS ORDER BY"),
                _FakeCursor(rows=[
                    ("cs1", "pro", "k1", "cus", issued, None),
                    ("cs2", "free", "k2", None, issued, claimed),
                ]),
            ),
        ])
        all_claims = store.claims_all()
        assert set(all_claims) == {"cs1", "cs2"}
        assert all_claims["cs1"]["plan_id"] == "pro"
        assert all_claims["cs1"]["claimed_at"] is None
        assert all_claims["cs2"]["claimed_at"] is not None

    def test_record_audit_success_and_swallows_errors(self, capsys):
        store = _store_with()
        store.record_audit(
            url="https://x",
            mode="standard",
            overall="PASS",
            latency_ms=12.5,
            caller_key="k",
            caller_plan="pro",
            source="public",
        )
        sql, params = store._pool.conns[-1].executes[0]
        assert "INSERT INTO X402_AUDITS" in sql.upper()
        assert params[0] == "https://x"
        assert params[6] == "public"

        def boom(_sql, _params):
            raise RuntimeError("db down")

        store2 = _store_with([(_has("INSERT INTO X402_AUDITS"), boom)])
        store2.record_audit(url="https://y", mode="standard")  # must not raise
        err = capsys.readouterr().err
        assert "record_audit failed" in err

    def test_usage_this_month(self):
        store = _store_with([
            (_has("COUNT(*)", "CALLER_KEY"), _FakeCursor(row=(7,))),
        ])
        assert store.usage_this_month("k") == 7

        store2 = _store_with([
            (_has("COUNT(*)"), _FakeCursor(row=None)),
        ])
        assert store2.usage_this_month("k") == 0

    def test_quota_allows_unknown_plan_and_limit(self):
        store = _store_with()
        store.usage_this_month = lambda key: 10  # type: ignore[method-assign]
        assert store.quota_allows("k", "unknown-plan") is True
        assert store.quota_allows("k", "free") is True
        store.usage_this_month = lambda key: 10_000  # type: ignore[method-assign]
        assert store.quota_allows("k", "free") is False
        assert store.quota_allows("k", None) is True  # unknown empty plan

    def test_audit_stats_with_mock_pool(self):
        def count_handler(sql, params):
            if "overall = 'PASS'" in sql:
                return _FakeCursor(row=(2,))
            if "date_trunc" in sql:
                return _FakeCursor(row=(5,))
            return _FakeCursor(row=(10,))

        store = _store_with([(_has("COUNT(*)"), count_handler)])
        assert store.audit_stats() == {
            "total": 10,
            "this_month": 5,
            "pass_this_month": 2,
        }

    def test_audit_stats_empty_rows_are_zero(self):
        store = _store_with([(_has("COUNT(*)"), _FakeCursor(row=None))])
        assert store.audit_stats() == {
            "total": 0,
            "this_month": 0,
            "pass_this_month": 0,
        }

    def test_close_and_pool_property(self):
        store = _store_with()
        assert store.pool is store._pool
        store.close()
        assert store._pool.closed is True


# ---------------------------------------------------------------------------
# Endpoint wiring: /validate must 429 when the store reports quota exhausted
# (no DB needed — the store is stubbed)
# ---------------------------------------------------------------------------


class TestValidateQuotaWiring:
    def test_validate_429_when_quota_exhausted(self, monkeypatch, tmp_path):
        import importlib
        import sys
        from fastapi.testclient import TestClient

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
        import api_server.keystore  # noqa: F401
        import api_server.app  # noqa: F401
        keystore_mod = sys.modules["api_server.keystore"]
        app_mod = sys.modules["api_server.app"]
        importlib.reload(keystore_mod)
        importlib.reload(app_mod)

        class QuotaExhaustedStore(keystore_mod.KeyStore):
            def quota_allows(self, key, plan_id):
                return False

        store = QuotaExhaustedStore(tmp_path / "quota.json")
        key = store.issue("pro")
        monkeypatch.setattr(app_mod, "get_store", lambda: store)

        client = TestClient(app_mod.app)
        r = client.post(
            "/validate",
            json={"url": "https://example.com", "mode": "standard"},
            headers={"X-API-Key": key},
        )
        assert r.status_code == 429
        assert "quota" in r.json()["detail"].lower()

    def test_validate_passes_when_quota_allows(self, monkeypatch, tmp_path):
        """Default JSON store (quota_allows=True) must not change behavior."""
        import importlib
        import sys
        from datetime import datetime, timezone
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
        import api_server.keystore  # noqa: F401
        import api_server.app  # noqa: F401
        keystore_mod = sys.modules["api_server.keystore"]
        app_mod = sys.modules["api_server.app"]
        importlib.reload(keystore_mod)
        importlib.reload(app_mod)

        key = app_mod.get_store().issue("pro")

        class _Check:
            check_name = "manifest_discovery"
            status = "PASS"
            message = "ok"
            details = None

        class _Report:
            target_url = "https://example.com"
            overall_status = "PASS"
            summary = "1/1 checks passed"
            timestamp = datetime(2026, 8, 7, tzinfo=timezone.utc)
            checks = [_Check()]

        async def fake_run_audit(url, mode, timeout=10.0):
            # _run_audit returns (report, probe, batch).
            return (
                _Report(),
                {
                    "check_name": "directory_cold_probe",
                    "status": "PASS",
                    "message": "ok",
                    "details": {"method": "POST", "status_code": 402},
                },
                {
                    "check_name": "batch_settlement_requirements",
                    "status": "PASS",
                    "message": "N/A",
                    "details": {"applicable": False, "payload_source": "none"},
                },
            )

        client = TestClient(app_mod.app)
        with patch.object(app_mod, "_run_audit", fake_run_audit):
            r = client.post(
                "/validate",
                json={"url": "https://example.com", "mode": "standard"},
                headers={"X-API-Key": key},
            )
        assert r.status_code == 200
        assert r.json()["overall"] == "PASS"


# ---------------------------------------------------------------------------
# PostgreSQL integration (skipped without TEST_DATABASE_URL)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (point it at a reachable PostgreSQL)",
)


@pytest.fixture(scope="module")
def db_store():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    from api_server.dbkeystore import DBKeyStore

    store = DBKeyStore(TEST_DATABASE_URL)
    yield store
    store.close()


@needs_db
class TestDBKeyStoreIntegration:
    def test_issue_get_contains(self, db_store):
        key = db_store.issue("pro", customer_id="cus_test")
        try:
            assert db_store.get(key) == "pro"
            assert key in db_store
            assert db_store[key] == "pro"
            assert key in db_store.all()
        finally:
            db_store.revoke(key)

    def test_revoke_missing_returns_false(self, db_store):
        assert db_store.revoke("nonexistent-token") is False

    def test_claim_lifecycle(self, db_store):
        session_id = "cs_test_lifecycle"
        key = db_store.issue("enterprise", customer_id="cus_c",
                             session_id=session_id)
        try:
            claim = db_store.claim_by_session(session_id)
            assert claim is not None
            assert claim["api_key"] == key
            assert claim["plan_id"] == "enterprise"
            assert claim["claimed_at"] is None

            assert db_store.mark_claimed(session_id) is True
            assert db_store.claim_by_session(session_id)["claimed_at"] is not None
            assert session_id in db_store.claims_all()
        finally:
            db_store.revoke(key)
        # revoking the key must cascade-delete the claim
        assert db_store.claim_by_session(session_id) is None

    def test_revoke_cascades_claims(self, db_store):
        session_id = "cs_test_cascade"
        key = db_store.issue("pro", session_id=session_id)
        assert db_store.claim_by_session(session_id) is not None
        db_store.revoke(key)
        assert db_store.claim_by_session(session_id) is None

    def test_claim_by_session_none_and_missing(self, db_store):
        assert db_store.claim_by_session(None) is None
        assert db_store.claim_by_session("cs_never_existed") is None
        assert db_store.mark_claimed("cs_never_existed") is False

    def test_audit_log_and_usage(self, db_store):
        key = db_store.issue("pro")
        try:
            before = db_store.usage_this_month(key)
            db_store.record_audit(
                url="https://example.com",
                mode="standard",
                overall="PASS",
                latency_ms=580.0,
                caller_key=key,
                caller_plan="pro",
                source="api",
            )
            assert db_store.usage_this_month(key) == before + 1

            stats = db_store.audit_stats()
            assert stats["total"] >= 1
            assert stats["this_month"] >= 1
        finally:
            db_store.revoke(key)

    def test_quota_enforcement(self, db_store):
        """A key over its plan quota must be refused by quota_allows."""
        from api_server.models import PLANS

        key = db_store.issue("free")  # free = 100/month
        limit = PLANS["free"].requests_per_month
        try:
            used = db_store.usage_this_month(key)
            for _ in range(limit - used):
                db_store.record_audit(
                    url="https://quota.test", mode="standard",
                    overall="FAIL", latency_ms=1.0,
                    caller_key=key, caller_plan="free", source="api",
                )
            assert db_store.quota_allows(key, "free") is False
        finally:
            db_store.revoke(key)

    def test_record_audit_never_raises(self, db_store):
        # Even garbage input must not propagate (metrics never break requests).
        db_store.record_audit(url=None, mode=None)  # type: ignore[arg-type]
