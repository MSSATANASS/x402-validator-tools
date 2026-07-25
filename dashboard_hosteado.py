from flask import Flask, render_template, request, redirect, session, jsonify
import sqlite3
import os
from datetime import datetime, timezone

app = Flask(__name__)
app.secret_key = os.environ.get("DASHBOARD_SECRET", "dev-secret-change-me")
DB_PATH = "data/hosted_dashboard.db"

os.makedirs("data", exist_ok=True)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            plan TEXT DEFAULT 'free',
            api_key TEXT UNIQUE,
            created_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS validations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT,
            overall_status TEXT,
            checks TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


init_db()


@app.route("/")
def index():
    return render_template("hosted_index.html", user=session.get("user"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"]
        import secrets
        api_key = "x402_" + secrets.token_hex(24)
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR IGNORE INTO users (email, plan, api_key, created_at) VALUES (?, 'free', ?, ?)",
            (email, api_key, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        session["user"] = {"email": email, "api_key": api_key, "plan": "free"}
        return redirect("/dashboard")
    return render_template("hosted_signup.html")


@app.route("/dashboard")
def dashboard():
    user = session.get("user")
    if not user:
        return redirect("/signup")
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT url, overall_status, checks, created_at FROM validations WHERE user_id = (SELECT id FROM users WHERE email = ?) ORDER BY created_at DESC LIMIT 50",
        (user["email"],),
    ).fetchall()
    conn.close()
    return render_template("hosted_dashboard.html", user=user, history=rows)


@app.route("/api/validate", methods=["POST"])
def api_validate():
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return jsonify({"error": "API key required"}), 401
    conn = sqlite3.connect(DB_PATH)
    user = conn.execute("SELECT id, plan FROM users WHERE api_key = ?", (api_key,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "Invalid API key"}), 401
    from x402_validator.conformance import run_single_audit
    import asyncio

    data = request.get_json()
    report = asyncio.run(run_single_audit(data["url"], timeout=10.0, mode=data.get("mode", "standard")))
    conn.execute(
        "INSERT INTO validations (user_id, url, overall_status, checks, created_at) VALUES (?, ?, ?, ?, ?)",
        (user[0], data["url"], report.overall_status, str([c.model_dump() for c in report.checks]),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify({"url": report.target_url, "overall": report.overall_status})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
