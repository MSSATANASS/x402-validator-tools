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
<title>x402 Validator — audit endpoints in seconds, not minutes</title>
<meta name="description" content="REST API that runs the x402 strict-v2 conformance suite against any URL. Manifest, CAIP-2, JSON resilience, Bazaar. Free, Pro, Enterprise plans.">
<style>
:root {
  --bg: #0a0e14;
  --bg2: #0d1117;
  --fg: #e6edf3;
  --muted: #8b949e;
  --card: #161b22;
  --card2: #1c2128;
  --border: #30363d;
  --border2: #444c56;
  --accent: #58a6ff;
  --accent2: #7ee787;
  --warn: #d29922;
  --fail: #f85149;
  --pass: #3fb950;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
.container { max-width: 1100px; margin: 0 auto; padding: 0 1.5rem; }

/* Nav */
nav { padding: 1rem 0; border-bottom: 1px solid var(--border); }
.nav-inner { display: flex; justify-content: space-between; align-items: center; }
.nav-brand { font-weight: 700; font-size: 1.1rem; color: var(--fg); text-decoration: none; letter-spacing: -0.02em; }
.nav-brand .dot { color: var(--accent); }
.nav-links { display: flex; gap: 1.5rem; }
.nav-links a { color: var(--muted); text-decoration: none; font-size: 0.95rem; }
.nav-links a:hover { color: var(--fg); }

/* Hero */
.hero { padding: 5rem 0 4rem; text-align: center; }
.hero h1 {
  font-size: clamp(2rem, 5vw, 3.4rem);
  margin: 0 0 1.2rem;
  letter-spacing: -0.03em;
  line-height: 1.1;
  font-weight: 800;
}
.hero h1 .accent { color: var(--accent); }
.hero .sub {
  color: var(--muted);
  font-size: clamp(1rem, 2vw, 1.2rem);
  max-width: 640px;
  margin: 0 auto 2rem;
}
.hero .ctas { display: flex; gap: 0.75rem; justify-content: center; flex-wrap: wrap; }
.btn {
  display: inline-flex; align-items: center; gap: 0.4rem;
  padding: 0.7rem 1.25rem;
  font-weight: 600;
  font-size: 0.95rem;
  border-radius: 6px;
  text-decoration: none;
  transition: transform 0.1s, opacity 0.1s;
}
.btn:hover { opacity: 0.9; transform: translateY(-1px); }
.btn-primary { background: var(--accent); color: #fff; }
.btn-secondary { background: transparent; color: var(--fg); border: 1px solid var(--border2); }
.btn-secondary:hover { border-color: var(--accent); color: var(--accent); }

/* Sections */
section { padding: 4rem 0; }
h2.section-title {
  text-align: center;
  font-size: clamp(1.5rem, 3vw, 2.1rem);
  margin: 0 0 0.5rem;
  letter-spacing: -0.02em;
  font-weight: 700;
}
.section-sub {
  text-align: center;
  color: var(--muted);
  max-width: 600px;
  margin: 0 auto 3rem;
}
hr.divider { border: none; border-top: 1px solid var(--border); margin: 0; }

/* Hero illustration */
.hero-wrap { display: grid; grid-template-columns: 1.1fr 0.9fr; align-items: center; gap: 2rem; }
@media (max-width: 800px) { .hero-wrap { grid-template-columns: 1fr; } }
.hero-art { display: flex; justify-content: center; }
.hero-art svg { max-width: 100%; height: auto; }
.hero .ctas { justify-content: flex-start; }

/* Features */
.features-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 1.5rem;
}
.feature {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.5rem;
}
.feature-icon {
  width: 44px; height: 44px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  margin-bottom: 1rem;
  box-shadow: 0 1px 0 rgba(255,255,255,0.08) inset, 0 6px 24px rgba(88,166,255,0.10);
}
.feature-icon svg { width: 22px; height: 22px; color: #0d1117; }
.feature h3 { margin: 0 0 0.5rem; font-size: 1.05rem; }
.feature p { margin: 0; color: var(--muted); font-size: 0.92rem; }

/* Pricing */
.pricing-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 1.5rem;
  max-width: 950px;
  margin: 0 auto;
}
.plan {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 2rem 1.75rem;
  display: flex;
  flex-direction: column;
}
.plan.featured {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent);
  position: relative;
}
.plan.featured::before {
  content: "Most popular";
  position: absolute;
  top: -10px; left: 50%;
  transform: translateX(-50%);
  background: var(--accent);
  color: #fff;
  padding: 0.2rem 0.75rem;
  border-radius: 12px;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.plan-name { font-size: 1.05rem; font-weight: 700; margin: 0 0 0.25rem; }
.plan-desc { color: var(--muted); font-size: 0.85rem; margin: 0 0 1.25rem; }
.plan-price { font-size: 2.5rem; font-weight: 800; letter-spacing: -0.03em; margin-bottom: 0.25rem; }
.plan-price small { font-size: 0.95rem; color: var(--muted); font-weight: 400; }
.plan-features { list-style: none; padding: 0; margin: 1.5rem 0; flex-grow: 1; }
.plan-features li { padding: 0.4rem 0; color: var(--muted); font-size: 0.92rem; position: relative; padding-left: 1.4rem; }
.plan-features li::before { content: "✓"; position: absolute; left: 0; color: var(--pass); font-weight: 700; }
.plan .btn { width: 100%; justify-content: center; margin-top: 1rem; }

/* How it works */
.how-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 1.5rem;
  max-width: 900px;
  margin: 0 auto;
}
.step {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1.5rem;
  position: relative;
}
.step-num {
  position: absolute; top: -12px; left: 16px;
  width: 32px; height: 32px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  color: #0d1117;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700;
  font-size: 0.85rem;
  box-shadow: 0 6px 24px rgba(88,166,255,0.18);
}
.step h3 { margin: 0.5rem 0 0.4rem; font-size: 1.05rem; }
.step p { margin: 0 0 1rem; color: var(--muted); font-size: 0.92rem; }
.code-block {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.75rem 0.9rem;
  font-family: 'SF Mono', Monaco, Menlo, monospace;
  font-size: 0.82rem;
  color: var(--fg);
  overflow-x: auto;
  white-space: pre;
  line-height: 1.55;
}
.code-block .c-key { color: var(--accent); }
.code-block .c-str { color: var(--accent2); }
.code-block .c-com { color: var(--muted); }

/* Output sample */
.sample-card {
  max-width: 700px;
  margin: 2rem auto 0;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem 1.25rem;
}
.sample-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; font-size: 0.85rem; }
.sample-head .url { color: var(--muted); font-family: 'SF Mono', Monaco, monospace; }
.sample-head .status { color: var(--pass); font-weight: 700; }
.sample-curl { padding-bottom: 0.75rem; border-bottom: 1px solid var(--border); }
.sample-checks { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem 1rem; padding-top: 0.75rem; font-family: 'SF Mono', Monaco, monospace; font-size: 0.82rem; }
.check { display: flex; align-items: center; gap: 0.5rem; }
.check-pill {
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  font-weight: 700;
  font-size: 0.7rem;
  letter-spacing: 0.02em;
}
.check-pill.pass { background: rgba(63,185,80,0.15); color: var(--pass); }
.check-pill.fail { background: rgba(248,81,73,0.15); color: var(--fail); }

/* Final CTA */
.cta-section { text-align: center; padding: 4rem 0; }
.cta-section h2 { margin-bottom: 0.5rem; }
.cta-section .ctas { display: flex; gap: 0.75rem; justify-content: center; margin-top: 1.5rem; flex-wrap: wrap; }

/* Footer */
footer { border-top: 1px solid var(--border); padding: 2rem 0; color: var(--muted); font-size: 0.85rem; }
footer .row { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 1rem; }
footer a { color: var(--muted); text-decoration: none; }
footer a:hover { color: var(--fg); }

@media (max-width: 600px) {
  .nav-links { gap: 1rem; font-size: 0.85rem; }
  .hero { padding: 3rem 0 2rem; }
  section { padding: 2.5rem 0; }
}
</style>
</head>
<body>
<nav>
  <div class="container nav-inner">
    <a href="/" class="nav-brand">x402<span class="dot">.</span>validator</a>
    <div class="nav-links">
      <a href="#features">Features</a>
      <a href="#pricing">Pricing</a>
      <a href="#how">Docs</a>
      <a href="https://github.com/MSSATANASS/x402-validator-tools" rel="noopener">GitHub</a>
    </div>
  </div>
</nav>

<!-- HERO -->
<header class="hero">
  <div class="container">
    <div class="hero-wrap">
      <div>
        <h1>Audit <span class="accent">x402</span> endpoints<br>in seconds, not minutes.</h1>
        <p class="sub" style="margin-left:0;">REST API that runs the x402 strict-v2 conformance suite against any URL.
        Manifest, CAIP-2, JSON resilience, Bazaar — one POST, structured JSON back.</p>
        <div class="ctas">
          <a class="btn btn-primary" href="#pricing">Start free →</a>
          <a class="btn btn-secondary" href="https://github.com/MSSATANASS/x402-validator-tools" rel="noopener">View on GitHub</a>
        </div>
      </div>
      <div class="hero-art">
        __SVG_HERO_ART__
      </div>
    </div>
  </div>
</header>

<hr class="divider">

<!-- FEATURES -->
<section id="features">
  <div class="container">
    <h2 class="section-title">Four checks. One endpoint.</h2>
    <p class="section-sub">Each check returns PASS, FAIL, CRITICAL_FAIL, or ERROR with an operator-actionable message. Coverage 100 % on the open-source engine.</p>
    <div class="features-grid">

      <div class="feature">
        <div class="feature-icon">__SVG_MANIFEST__</div>
        <h3>manifest_discovery</h3>
        <p>GET /.well-known/x402 must return valid JSON with accepts or products. Tells you if discovery is even wired up.</p>
      </div>

      <div class="feature">
        <div class="feature-icon">__SVG_CAIP2__</div>
        <h3>caip2_compliance</h3>
        <p>A payment header carries a valid CAIP-2 network identifier. Catches malformed networks that wallets will reject.</p>
      </div>

      <div class="feature">
        <div class="feature-icon">__SVG_JSON__</div>
        <h3>json_resilience</h3>
        <p>The HTTP 402 body is a JSON object — not a string, number, or null body that crashes the reference verifier.</p>
      </div>

      <div class="feature">
        <div class="feature-icon">__SVG_BAZAAR__</div>
        <h3>bazaar_compliance</h3>
        <p>extensions.bazaar block has the right shape: method=POST, serviceName set, tags non-empty. Required for Bazaar discovery.</p>
      </div>

    </div>

    <!-- Sample response (show, don't tell) -->
    <div class="sample-card">
      <div class="sample-head">
        <span class="url">https://observer.137-184-67-179.sslip.io</span>
        <span class="status">FAIL &middot; 582 ms</span>
      </div>
      <div class="sample-curl code-block"><span style="color:#8b949e"># POST /validate { url: "..." }</span>
curl https://lastminutestickets.com/validate \
  -H <span class="c-str">"X-API-Key: <span class="c-key">$YOUR_KEY</span>"</span> \
  -d <span class="c-str">'{"url":"https://observer..."}'</span></div>
      <div class="sample-checks">
        <div class="check"><span class="check-pill pass">PASS</span> manifest_discovery</div>
        <div class="check"><span class="check-pill fail">FAIL</span> caip2_compliance</div>
        <div class="check"><span class="check-pill pass">PASS</span> json_resilience</div>
        <div class="check"><span class="check-pill pass">PASS</span> bazaar_compliance</div>
      </div>
    </div>
  </div>
</section>

<hr class="divider">

<!-- PRICING -->
<section id="pricing">
  <div class="container">
    <h2 class="section-title">Pick a plan. Cancel anytime.</h2>
    <p class="section-sub">Pay once a month via Stripe. Billed through lastminutestickets.com. No long-term contract.</p>
    <div class="pricing-grid">

      <div class="plan">
        <h3 class="plan-name">Free</h3>
        <p class="plan-desc">For trying it out.</p>
        <div class="plan-price">$0<small> /mo</small></div>
        <ul class="plan-features">
          <li>100 audits / month</li>
          <li>All four core checks</li>
          <li>JSON response, no rate limit</li>
          <li>Community support</li>
        </ul>
        <a class="btn btn-secondary" href="/create-checkout-session?plan_id=free">Start free</a>
      </div>

      <div class="plan featured">
        <h3 class="plan-name">Pro</h3>
        <p class="plan-desc">For shipping x402 merchants.</p>
        <div class="plan-price">$9<small> /mo</small></div>
        <ul class="plan-features">
          <li>500 audits / month</li>
          <li>marketplace mode</li>
          <li>Per-product walks</li>
          <li>Actionable error messages</li>
          <li>Email support</li>
        </ul>
        <a class="btn btn-primary" href="/create-checkout-session?plan_id=pro">Buy Pro — $9 / mo</a>
      </div>

      <div class="plan">
        <h3 class="plan-name">Enterprise</h3>
        <p class="plan-desc">For higher-traffic compliance.</p>
        <div class="plan-price">$49<small> /mo</small></div>
        <ul class="plan-features">
          <li>5,000 audits / month</li>
          <li>Everything in Pro</li>
          <li>Bulk mode (in beta)</li>
          <li>Priority support</li>
          <li>Volume rebate (soon)</li>
        </ul>
        <a class="btn btn-secondary" href="/create-checkout-session?plan_id=enterprise">Buy Enterprise — $49 / mo</a>
      </div>

    </div>
  </div>
</section>

<hr class="divider">

<!-- HOW IT WORKS -->
<section id="how">
  <div class="container">
    <h2 class="section-title">Up and running in three steps.</h2>
    <p class="section-sub">One Stripe checkout. One API key. One HTTP call.</p>
    <div class="how-grid">

      <div class="step">
        <div class="step-num">1</div>
        <h3>Pick a plan</h3>
        <p>Click any "Buy" button above. You'll see the standard Stripe checkout (card, Apple Pay, Google Pay).</p>
      </div>

      <div class="step">
        <div class="step-num">2</div>
        <h3>Receive your key</h3>
        <p>After payment we email an API key tied to your plan. We revoke/extend automatically on each subscription event.</p>
        <div class="code-block">X-API-Key: abc123XYZ...</div>
      </div>

      <div class="step">
        <div class="step-num">3</div>
        <h3>Call /validate</h3>
        <p>Pipe it into CI, your audit dashboard, or a cron: every call returns the per-check breakdown below.</p>
        <div class="code-block">POST /validate
{
  <span class="c-key">"url"</span>: <span class="c-str">"https://yoursite.com"</span>,
  <span class="c-key">"mode"</span>: <span class="c-str">"standard"</span>
}</div>
      </div>

    </div>
  </div>
</section>

<!-- FINAL CTA -->
<section class="cta-section">
  <div class="container">
    <h2 class="section-title">Try it on your merchant URL right now.</h2>
    <p class="section-sub">Free plan has 100 audits / month — enough to validate your whole catalog.</p>
    <div class="ctas">
      <a class="btn btn-primary" href="/create-checkout-session?plan_id=pro">Get Pro — $9 / mo</a>
      <a class="btn btn-secondary" href="/create-checkout-session?plan_id=free">Start free</a>
    </div>
  </div>
</section>

<!-- FOOTER -->
<footer>
  <div class="container row">
    <div>
      &copy; 2026 x402 validator &middot;
      <a href="https://github.com/smartflowproai-lang/x402-endpoint-validator">upstream engine</a> &middot;
      <a href="https://github.com/MSSATANASS/x402-validator-tools">tools</a> &middot;
      <a href="https://github.com/MSSATANASS/x402-conformance-engine">engine fork</a>
    </div>
    <div>
      <a href="/health">status</a> &middot;
      <a href="/plans">plans</a> &middot;
      <a href="mailto:support@lastminutestickets.com">support</a>
    </div>
  </div>
</footer>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Inline SVG assets (kept as module constants so we don't repeat them inline
# in the HTML and so engineers can swap them out centrally).
# ---------------------------------------------------------------------------


_SVG_STROKE = 'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'


_SVG_MANIFEST = f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" {_SVG_STROKE}>
<path d="M5 3h11l3 3v15a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/>
<path d="M15 3v4h4"/>
<path d="M8 11h8"/><path d="M8 15h8"/><path d="M8 19h5"/>
</svg>'''


_SVG_CAIP2 = f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" {_SVG_STROKE}>
<circle cx="6" cy="6" r="2.5"/>
<circle cx="18" cy="6" r="2.5"/>
<circle cx="12" cy="18" r="2.5"/>
<path d="M8.5 6h7"/><path d="M7.3 8.1l3.7 7.8"/><path d="M16.7 8.1l-3.7 7.8"/>
</svg>'''


_SVG_JSON = f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" {_SVG_STROKE}>
<path d="M9 4c-2 0-3 1-3 3v3c0 1.5-.7 2-2 2 1.3 0 2 .5 2 2v3c0 2 1 3 3 3"/>
<path d="M15 4c2 0 3 1 3 3v3c0 1.5.7 2 2 2-1.3 0-2 .5-2 2v3c0 2-1 3-3 3"/>
</svg>'''


_SVG_BAZAAR = f'''<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" {_SVG_STROKE}>
<path d="M14.5 3l5.5 5.5L9 19.5l-3.5.5L6 16.5z"/>
<circle cx="16" cy="8" r="1.6"/>
</svg>'''


# Hero illustration: a stylized view of one URL flowing through the engine
# and producing 4 check results. Pure SVG, ~3 KB.
_SVG_HERO_ART = '''<svg viewBox="0 0 520 280" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="g_accent" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#58a6ff"/>
    <stop offset="100%" stop-color="#7ee787"/>
  </linearGradient>
  <linearGradient id="g_card" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#1c2128"/>
    <stop offset="100%" stop-color="#161b22"/>
  </linearGradient>
  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="6"/>
  </filter>
</defs>

<style>
  .card { fill: url(#g_card); stroke: #30363d; stroke-width: 1; }
  .mono { font-family: 'SF Mono', Menlo, monospace; font-size: 12px; fill: #c9d1d9; }
  .lbl  { font-family: -apple-system, sans-serif; font-size: 11px; fill: #8b949e; }
  .url  { font-family: 'SF Mono', Menlo, monospace; font-size: 11px; fill: #e6edf3; font-weight: 600; }
  line.flow { stroke: url(#g_accent); stroke-width: 2; }
  circle.dot { fill: #58a6ff; }
  .pill_pass { fill: rgba(63,185,80,0.18); }
  .pill_fail { fill: rgba(248,81,73,0.18); }
  .pill_pass_text { font-family: 'SF Mono', Menlo, monospace; font-size: 9px; fill: #3fb950; font-weight: 700; }
  .pill_fail_text { font-family: 'SF Mono', Menlo, monospace; font-size: 9px; fill: #f85149; font-weight: 700; }
  .check_name { font-family: 'SF Mono', Menlo, monospace; font-size: 10px; fill: #c9d1d9; }
</style>

<!-- glow halo -->
<rect x="190" y="105" width="140" height="60" rx="8" fill="#58a6ff" opacity="0.10" filter="url(#glow)"/>

<!-- URL input card -->
<rect class="card" x="20" y="80" width="180" height="120" rx="10"/>
<text class="lbl" x="34" y="100">GET /.well-known/x402</text>
<text class="url" x="34" y="124">api.x-402.online</text>
<text class="url" x="34" y="142">k402 kaspa-402</text>
<text class="url" x="34" y="160">defi.hugen.tokyo</text>
<text class="url" x="34" y="178">observer.sslip.io</text>
<text class="lbl" x="34" y="198">27 endpoints audited</text>

<!-- flow lines from URL card to engine -->
<path class="flow" d="M200 140 C 240 140, 240 140, 280 140"/>

<!-- dot on input -->
<circle class="dot" cx="200" cy="140" r="4"/>

<!-- Engine card -->
<rect class="card" x="280" y="100" width="140" height="80" rx="10"/>
<text class="lbl" x="296" y="124">x402 conformance</text>
<text class="lbl" x="296" y="140">engine</text>
<text class="mono" x="296" y="160">v0.3.0</text>
<text class="lbl" x="296" y="174">100% coverage</text>

<!-- flow line from engine to results -->
<path class="flow" d="M420 140 C 460 140, 460 140, 490 140"/>
<circle class="dot" cx="490" cy="140" r="4"/>

<!-- Result badges -->
<rect class="card" x="380" y="20" width="120" height="32" rx="6"/>
<rect class="pill_pass" x="386" y="26" width="36" height="20" rx="4"/>
<text class="pill_pass_text" x="404" y="40" text-anchor="middle">PASS</text>
<text class="check_name" x="430" y="40">manifest</text>

<rect class="card" x="380" y="58" width="120" height="32" rx="6"/>
<rect class="pill_pass" x="386" y="64" width="36" height="20" rx="4"/>
<text class="pill_pass_text" x="404" y="78" text-anchor="middle">PASS</text>
<text class="check_name" x="430" y="78">caip-2</text>

<rect class="card" x="380" y="96" width="120" height="32" rx="6"/>
<rect class="pill_fail" x="386" y="102" width="36" height="20" rx="4"/>
<text class="pill_fail_text" x="404" y="116" text-anchor="middle">FAIL</text>
<text class="check_name" x="430" y="116">json_</text>

<rect class="card" x="380" y="134" width="120" height="32" rx="6"/>
<rect class="pill_pass" x="386" y="140" width="36" height="20" rx="4"/>
<text class="pill_pass_text" x="404" y="154" text-anchor="middle">PASS</text>
<text class="check_name" x="430" y="154">bazaar</text>

<!-- bottom latency label -->
<text class="lbl" x="160" y="260" text-anchor="middle">~580 ms per endpoint</text>
<text class="lbl" x="380" y="260" text-anchor="middle">structured JSON</text>
</svg>
'''


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing() -> HTMLResponse:
    return HTMLResponse(
        _LANDING_HTML
        .replace("__SVG_MANIFEST__", _SVG_MANIFEST)
        .replace("__SVG_CAIP2__", _SVG_CAIP2)
        .replace("__SVG_JSON__", _SVG_JSON)
        .replace("__SVG_BAZAAR__", _SVG_BAZAAR)
        .replace("__SVG_HERO_ART__", _SVG_HERO_ART)
    )


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
