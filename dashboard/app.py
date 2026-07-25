import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from x402_conformance_engine import X402Auditor

app = Flask(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = DATA_DIR / "results.json"


def load_results() -> list[dict]:
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            return json.load(f)
    return []


def save_results(results: list[dict]) -> None:
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)


@app.route("/", methods=["GET", "POST"])
def index():
    results = load_results()

    if request.method == "POST":
        url = request.form.get("url", "").strip()
        if url:
            return redirect(url_for("validate_url", url=url))

    return render_template("index.html", results=results)


@app.route("/validate/<path:url>")
async def validate_url(url: str):
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    results = load_results()

    try:
        async with X402Auditor() as auditor:
            report = await auditor.run_full_audit(url)
    except Exception as e:
        return render_template("error.html", url=url, error=str(e))

    record = {
        "id": secrets.token_hex(8),
        "url": url,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall_status": report.overall_status,
        "summary": report.summary,
        "checks": [
            {
                "name": c.check_name,
                "status": c.status,
                "message": c.message,
            }
            for c in report.checks
        ],
    }

    results.insert(0, record)
    save_results(results)

    return render_template("report.html", record=record)


@app.route("/report/<record_id>")
def report(record_id: str):
    results = load_results()
    record = next((r for r in results if r["id"] == record_id), None)
    if not record:
        return render_template("error.html", error="Report not found"), 404
    return render_template("report.html", record=record)


@app.route("/api/validate/<path:url>")
async def api_validate(url: str):
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"

    try:
        async with X402Auditor() as auditor:
            report = await auditor.run_full_audit(url)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "url": url,
        "overall_status": report.overall_status,
        "summary": report.summary,
        "checks": [
            {"name": c.check_name, "status": c.status, "message": c.message}
            for c in report.checks
        ],
    })


@app.route("/api/history")
def api_history():
    results = load_results()
    return jsonify(results[:100])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
