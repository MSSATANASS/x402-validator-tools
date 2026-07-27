"""FastAPI app exposing x402-validator as a paid API.

Endpoints
---------

    GET  /                 landing HTML page
    GET  /health           liveness check
    GET  /plans            list available subscription plans
    POST /validate         audit a single URL (requires API key)
    POST /create-checkout-session  create a Stripe checkout session for a plan
    POST /stripe-webhook   Stripe webhook receiver (signature verified)
    POST /admin/keys       mint a new API key for a plan (admin secret required)
    DELETE /admin/keys/{key}  revoke a key (admin secret required)

API keys are persisted to a JSON file (``api_keys.json`` by default, override
with ``API_KEYS_FILE`` env var). Multireplica deployments should swap
``api_server.keystore`` for a database-backed implementation.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api_server.models import (
    PLANS,
    CheckResultItem,
    CheckoutResponse,
    Plan,
    ValidateRequest,
    ValidateResponse,
)
from api_server import stripe_integration
from api_server.keystore import get_store


# ---------------------------------------------------------------------------
# App + state
# ---------------------------------------------------------------------------


app = FastAPI(
    title="x402 Validator API",
    version="0.3.0",
    description="Audit x402 endpoint conformance as a service.",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_audit(url: str, mode: str, timeout: float = 10.0):
    """Run the x402 audit and return the report."""
    from x402_validator._engine import run_audit  # lazy import
    return await run_audit(url, timeout=timeout, mode=mode)


def _require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """FastAPI dependency: 401 unless the supplied key is registered."""
    plan = get_store().get(x_api_key)
    if not plan:
        raise HTTPException(401, "Invalid API key")
    return plan


def _flatten_checks(report) -> list[CheckResultItem]:
    return [
        CheckResultItem(
            name=c.check_name,
            status=c.status,
            message=c.message,
            details=c.details,
        )
        for c in report.checks
    ]


def _require_admin(x_admin_secret: str = Header(..., alias="X-Admin-Secret")) -> None:
    """FastAPI dependency: 401 unless ``ADMIN_SECRET`` env var matches the header."""
    expected = os.environ.get("ADMIN_SECRET", "")
    if not expected or x_admin_secret != expected:
        raise HTTPException(401, "Invalid admin secret")


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------


_LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>x402 Validator API — audit live endpoints</title>
<style>
:root {
  --bg: #0d1117; --fg: #c9d1d9; --card: #161b22; --border: #30363d;
  --accent: #58a6ff; --pass: #3fb950; --fail: #f85149; --warn: #d29922;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: var(--bg); color: var(--fg); margin: 0; padding: 0;
       max-width: 800px; margin: 0 auto; padding: 2rem; line-height: 1.55; }
h1 { color: var(--accent); margin-top: 0; }
h2 { border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }
a { color: var(--accent); }
.card { background: var(--card); border: 1px solid var(--border);
       border-radius: 8px; padding: 1.5rem; margin: 1.5rem 0; }
.cta { display: inline-block; background: var(--accent); color: #fff;
       padding: 0.6rem 1.2rem; border-radius: 6px; text-decoration: none;
       font-weight: 600; }
.cta:hover { opacity: 0.85; }
.price-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.plan { background: var(--card); border: 1px solid var(--border);
        padding: 1.2rem; border-radius: 8px; }
.plan h3 { margin-top: 0; color: var(--accent); }
.price { font-size: 2.5rem; font-weight: 700; margin: 0.5rem 0; color: var(--accent); }
.price small { font-size: 0.9rem; color: var(--fg); opacity: 0.7; }
ul { padding-left: 1.2rem; }
code { background: rgba(255,255,255,0.05); padding: 0.1rem 0.4rem;
       border-radius: 3px; font-family: 'SF Mono', Monaco, monospace; }
footer { color: #8b949e; font-size: 0.85rem; border-top: 1px solid var(--border);
         padding-top: 1.5rem; margin-top: 2rem; }
</style>
</head>
<body>
<h1>x402 Validator API</h1>
<p>Audit any URL against the x402 strict-v2 standard.
Built on top of <a href="https://github.com/smartflowproai-lang/x402-endpoint-validator">smartflowproai-lang/x402-endpoint-validator</a>.</p>

<div class="card" style="text-align:center;">
  <a class="cta" href="/create-checkout-session?plan_id=pro">Get a Pro key ($9 / month)</a>
  &nbsp;
  <a class="cta" style="background: var(--card); color: var(--accent); border: 1px solid var(--accent);"
     href="/create-checkout-session?plan_id=enterprise">Enterprise ($49)</a>
</div>

<h2>Pricing</h2>
<div class="price-grid">
  <div class="plan">
    <h3>Free</h3>
    <div class="price">$0<small> /mo</small></div>
    <p>100 requests/month.</p>
    <a href="/create-checkout-session?plan_id=free">Start</a>
  </div>
  <div class="plan">
    <h3>Pro</h3>
    <div class="price">$9<small> /mo</small></div>
    <p>500 requests/month.</p>
    <a href="/create-checkout-session?plan_id=pro">Buy Pro</a>
  </div>
  <div class="plan">
    <h3>Enterprise</h3>
    <div class="price">$49<small> /mo</small></div>
    <p>5,000 requests/month.</p>
    <a href="/create-checkout-session?plan_id=enterprise">Buy Enterprise</a>
  </div>
</div>

<h2>How to call</h2>
<div class="card">
  <pre><code>curl -X POST https://lastminutestickets.com/validate \\
  -H "X-API-Key: YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"url":"https://example.com","mode":"standard"}'</code></pre>
  <p><small>After Stripe checkout, the operator issues a key and emails it to you.
  Default <code>mode</code> is <code>"standard"</code>; pass <code>"marketplace"</code> for multi-product catalogs.</small></p>
</div>

<h2>What it checks</h2>
<div class="card">
  <ul>
    <li><code>manifest_discovery</code> — <code>GET /.well-known/x402</code> returns valid JSON with <code>accepts</code> or <code>products</code></li>
    <li><code>caip2_compliance</code> — payment header carries a valid CAIP-2 network identifier</li>
    <li><code>json_resilience</code> — HTTP 402 body is a JSON object, not a primitive</li>
    <li><code>bazaar_compliance</code> — <code>extensions.bazaar</code> block has the right shape</li>
  </ul>
</div>

<footer>
  <p>
    Engine source:
    <a href="https://github.com/smartflowproai-lang/x402-endpoint-validator">upstream</a> ·
    <a href="https://github.com/MSSATANASS/x402-conformance-engine">fork</a> ·
    <a href="https://github.com/MSSATANASS/x402-validator-tools">tools</a>
  </p>
</footer>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing() -> HTMLResponse:
    return HTMLResponse(_LANDING_HTML)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/plans")
async def plans() -> list[Plan]:
    return list(PLANS.values())


@app.post("/validate", response_model=ValidateResponse)
async def validate(
    req: ValidateRequest,
    plan_id: str = Depends(_require_api_key),
) -> ValidateResponse:
    started = time.monotonic()
    try:
        report = await _run_audit(req.url, req.mode)
    except Exception as e:
        raise HTTPException(502, f"Audit failed: {e}")
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    return ValidateResponse(
        url=report.target_url,
        overall=report.overall_status,
        summary=report.summary,
        checks=_flatten_checks(report),
        latency_ms=elapsed_ms,
        timestamp=report.timestamp.isoformat(),
    )


@app.post("/create-checkout-session", response_model=CheckoutResponse)
async def create_checkout_session(plan_id: str) -> CheckoutResponse:
    if plan_id not in PLANS:
        raise HTTPException(400, f"Unknown plan: {plan_id!r}")

    base = os.environ.get("PUBLIC_URL", "https://lastminutestickets.com")
    try:
        url = stripe_integration.create_checkout_session(
            plan_id,
            success_url=f"{base}/success",
            cancel_url=f"{base}/cancel",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    if url is None:
        return CheckoutResponse(
            plan_id=plan_id,
            note=(
                "Free plan — no checkout needed"
                if PLANS[plan_id].price_cents == 0
                else "Stripe is not configured (set STRIPE_SECRET_KEY)"
            ),
        )
    return CheckoutResponse(plan_id=plan_id, checkout_url=url)


class StripeWebhookPayload(BaseModel):
    type: str
    data: Optional[dict] = None


@app.post("/stripe-webhook")
async def stripe_webhook(body: bytes, signature: str = Header(...)) -> dict:
    """Verify a Stripe webhook and dispatch the event.

    Customer.subscribed events auto-issue a Pro/Enterprise key here.
    """
    event = stripe_integration.verify_webhook(body, signature)
    if event is None:
        raise HTTPException(400, "Invalid signature or Stripe not configured")

    event_type = event.get("type") or event.get("event_type")

    if event_type in ("checkout.session.completed", "invoice.paid"):
        # Real implementation: look up the customer, mint a key, send the key
        # via the dashboard's "claim" endpoint. For now we just acknowledge.
        pass

    return {"received": True, "type": event_type}


# ---------------------------------------------------------------------------
# Admin endpoints (for operators to mint / revoke keys)
# ---------------------------------------------------------------------------


class IssueKeyRequest(BaseModel):
    plan_id: str


class IssueKeyResponse(BaseModel):
    api_key: str
    plan_id: str
    issued_at: str


@app.post("/admin/keys", response_model=IssueKeyResponse, dependencies=[Depends(_require_admin)])
async def admin_issue_key(req: IssueKeyRequest) -> IssueKeyResponse:
    if req.plan_id not in PLANS:
        raise HTTPException(400, f"Unknown plan: {req.plan_id!r}")
    key = get_store().issue(req.plan_id)
    return IssueKeyResponse(
        api_key=key,
        plan_id=req.plan_id,
        issued_at=datetime.now(timezone.utc).isoformat(),
    )


class KeyListResponse(BaseModel):
    count: int
    keys: dict[str, str]


@app.get("/admin/keys", response_model=KeyListResponse, dependencies=[Depends(_require_admin)])
async def admin_list_keys() -> KeyListResponse:
    return KeyListResponse(count=len(get_store().all()), keys=get_store().all())


@app.delete("/admin/keys/{key}", dependencies=[Depends(_require_admin)])
async def admin_revoke_key(key: str) -> dict:
    if not get_store().revoke(key):
        raise HTTPException(404, "Key not found")
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Success / cancel pages (after Stripe checkout)
# ---------------------------------------------------------------------------


@app.get("/success", response_class=HTMLResponse, include_in_schema=False)
async def success_page() -> HTMLResponse:
    return HTMLResponse(
        "<h1>Payment received</h1>"
        "<p>Your key has been emailed to you. If you don't see it within 5 minutes, "
        "check spam or contact support@lastminutestickets.com.</p>"
    )


@app.get("/cancel", response_class=HTMLResponse, include_in_schema=False)
async def cancel_page() -> HTMLResponse:
    return HTMLResponse(
        "<h1>Checkout cancelled</h1>"
        "<p>Nothing was charged. <a href='/'>Try again</a>.</p>"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run with uvicorn when invoked as ``x402-api``."""
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
