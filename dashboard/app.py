"""Flask dashboard for browsing x402 conformance audit history.

Three routes:

    /                     form + recent history
    /audit/<url>          run + display a single URL audit
    /report/<id>          display a previously-saved audit
    /api/audit/<url>      JSON variant of /audit
    /api/history          JSON list of recent audits

History is stored in ``dashboard/data/results.json``. In-memory state is fine
for single-process deployments; multi-replica deployments should swap it for
an external store (Redis, DynamoDB, etc.).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for


DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = DATA_DIR / "results.json"


app = Flask(__name__, template_folder="templates", static_folder="static")


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _load_results() -> list[dict]:
    if not RESULTS_FILE.exists():
        return []
    try:
        return json.loads(RESULTS_FILE.read_text())
    except json.JSONDecodeError:
        return []


def _save_results(results: list[dict]) -> None:
    RESULTS_FILE.write_text(json.dumps(results, indent=2, default=str))


def _record(report) -> dict:
    return {
        "id": secrets.token_hex(8),
        "url": report.target_url,
        "timestamp": report.timestamp.isoformat(),
        "overall_status": report.overall_status,
        "summary": report.summary,
        "checks": [
            {
                "name": c.check_name,
                "status": c.status,
                "message": c.message,
                "details": c.details,
            }
            for c in report.checks
        ],
    }


# ---------------------------------------------------------------------------
# Engine bridge
# ---------------------------------------------------------------------------


def _run_audit_sync(url: str, mode: str = "standard"):
    """Run the async audit and return the report (sync wrapper for Flask)."""
    from x402_conformance_suite._engine import run_audit  # lazy import
    return asyncio.run(run_audit(url, timeout=10.0, mode=mode))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET", "POST"])
def index() -> Any:
    results = _load_results()
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        if url:
            return redirect(url_for("audit_url", url=url))
    return render_template("index.html", results=results[:20])


@app.route("/audit/<path:url>")
def audit_url(url: str) -> Any:
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    try:
        report = _run_audit_sync(url)
    except Exception as e:
        return render_template("error.html", url=url, error=str(e)), 502

    record = _record(report)
    results = _load_results()
    results.insert(0, record)
    _save_results(results[:200])  # cap at 200 most recent
    return render_template("report.html", record=record)


@app.route("/report/<record_id>")
def report_view(record_id: str) -> Any:
    record = next((r for r in _load_results() if r["id"] == record_id), None)
    if not record:
        return render_template("error.html", error=f"Report {record_id} not found"), 404
    return render_template("report.html", record=record)


@app.route("/api/audit/<path:url>")
def api_audit(url: str) -> Any:
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        report = _run_audit_sync(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(_record(report))


@app.route("/api/history")
def api_history() -> Any:
    return jsonify(_load_results()[:100])


@app.route("/health")
def health() -> Any:
    return jsonify({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    host = __import__("os").environ.get("HOST", "0.0.0.0")
    port = int(__import__("os").environ.get("PORT", "5000"))
    debug = __import__("os").environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
