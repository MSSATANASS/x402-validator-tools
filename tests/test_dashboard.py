"""Tests for the Flask dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from dashboard.app import _record, _save_results, _load_results
from dashboard import app as dashboard_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Point RESULTS_FILE at a tmp path so tests don't leak."""
    monkeypatch.setattr(dashboard_app, "RESULTS_FILE", tmp_path / "results.json")
    return dashboard_app.app.test_client()


@pytest.fixture
def fake_report():
    """Minimal AuditReport-like object."""

    class _Check:
        def __init__(self, name, status, message, details=None):
            self.check_name = name
            self.status = status
            self.message = message
            self.details = details

    class _Report:
        def __init__(self):
            self.target_url = "https://example.com"
            # Real AuditReport uses a datetime (UTC). The dashboard code calls
            # .isoformat() on it; tests have to match.
            self.timestamp = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
            self.overall_status = "PASS"
            self.summary = "4/4 checks passed"
            self.checks = [
                _Check("manifest_discovery", "PASS", "ok"),
                _Check("caip2_compliance", "FAIL", "no CAIP-2 found"),
            ]

    return _Report()


class TestIndex:
    def test_get_returns_empty_state(self, client) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert b"No audits yet" in r.data or b"<title>" in r.data

    def test_post_redirects_to_audit(self, client) -> None:
        r = client.post("/", data={"url": "https://example.com"})
        assert r.status_code == 302
        assert "/audit/https://example.com" in r.headers["Location"]


class TestAuditUrl:
    def test_runs_audit_and_renders(self, client, fake_report) -> None:
        async def fake_run_audit(url: str, **_kw):
            return fake_report

        # dashboard.app calls asyncio.run(run_audit(...)) directly
        # We patch _run_audit_sync to bypass the import path
        with patch.object(dashboard_app, "_run_audit_sync", return_value=fake_report):
            r = client.get("/audit/https://example.com")
        assert r.status_code == 200
        assert b"PASS" in r.data
        assert b"manifest_discovery" in r.data

    def test_normalizes_url_without_scheme(self, client, fake_report) -> None:
        with patch.object(dashboard_app, "_run_audit_sync", return_value=fake_report) as m:
            r = client.get("/audit/example.com")
        assert r.status_code == 200
        # The function got called with normalised URL
        assert m.called
        call_url = m.call_args[0][0]
        assert call_url.startswith("https://")

    def test_records_in_history(self, client, fake_report, tmp_path, monkeypatch) -> None:
        with patch.object(dashboard_app, "_run_audit_sync", return_value=fake_report):
            client.get("/audit/https://example.com")
        results = _load_results()
        assert len(results) == 1
        assert results[0]["url"] == "https://example.com"
        assert results[0]["overall_status"] == "PASS"


class TestReportView:
    def test_renders_existing_record(self, client, fake_report, monkeypatch) -> None:
        with patch.object(dashboard_app, "_run_audit_sync", return_value=fake_report):
            client.get("/audit/https://example.com")
        record_id = _load_results()[0]["id"]
        r = client.get(f"/report/{record_id}")
        assert r.status_code == 200

    def test_404_for_unknown(self, client) -> None:
        r = client.get("/report/nope")
        assert r.status_code == 404


class TestApiAudit:
    def test_returns_json(self, client, fake_report) -> None:
        with patch.object(dashboard_app, "_run_audit_sync", return_value=fake_report):
            r = client.get("/api/audit/https://example.com")
        assert r.status_code == 200
        body = r.get_json()
        assert body["url"] == "https://example.com"
        assert body["overall_status"] == "PASS"


class TestAuditError:
    def test_renders_error_template_on_failure(self, client) -> None:
        with patch.object(
            dashboard_app, "_run_audit_sync", side_effect=RuntimeError("boom")
        ):
            r = client.get("/audit/https://example.com")
        assert r.status_code == 502
        assert b"Audit failed" in r.data


class TestRecord:
    def test__record_flattens_checks(self, fake_report) -> None:
        rec = _record(fake_report)
        assert rec["url"] == "https://example.com"
        assert rec["overall_status"] == "PASS"
        assert len(rec["checks"]) == 2
        assert rec["checks"][0]["name"] == "manifest_discovery"
