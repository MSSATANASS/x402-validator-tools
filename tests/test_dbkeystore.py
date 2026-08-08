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
            return _Report()

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
