"""FastAPI app exposing the x402 conformance suite as a paid API.

Endpoints
---------

    GET  /                 landing HTML page
    GET  /health           liveness check
    GET  /plans            list available subscription plans
    POST /validate         audit a single URL (requires API key; ``advise:
                           true`` attaches Qwen AI remediation advice when
                           ``DASHSCOPE_API_KEY`` is configured)
    POST /create-checkout-session  create a Stripe checkout session for a plan
    POST /stripe-webhook   Stripe webhook receiver (signature verified)
    POST /admin/keys       mint a new API key for a plan (admin secret required)
    DELETE /admin/keys/{key}  revoke a key (admin secret required)

API keys are persisted to a JSON file (``api_keys.json`` by default, override
with ``API_KEYS_FILE`` env var). Setting ``DATABASE_URL`` switches to the
PostgreSQL / PolarDB-backed store (``api_server.dbkeystore``), which also
enables per-plan monthly quota enforcement and the audit log behind /open.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
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
from api_server import ratelimit
from api_server import ai_advisor
from api_server import auth
from api_server import auth_pages
from api_server.keystore import get_store
from api_server.pages import (
    PAGE_CSS as _PAGE_CSS,
    PAGE_FOOTER as _PAGE_FOOTER,
    PAGE_NAV as _PAGE_NAV,
    auth_nav_links as _auth_nav_links,
)


# ---------------------------------------------------------------------------
# App + state
# ---------------------------------------------------------------------------


app = FastAPI(
    title="x402 Validator API",
    version="0.3.0",
    description="Audit x402 endpoint conformance as a service.",
)

# Static assets (logo, favicon, og-image) live alongside this package.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_audit(url: str, mode: str, timeout: float = 10.0):
    """Run engine audit + directory cold probe, then batch-settlement check.

    Returns ``(report, probe, batch)``:
    - ``report``: engine ``AuditReport``
    - ``probe``: CheckResult-shaped dict from the cold POST
    - ``batch``: CheckResult-shaped dict from
      ``evaluate_batch_settlement_requirements``

    Payload for batch prefers the cold-probe 402 snapshot (zero extra
    fetches). Otherwise one never-raise GET fallback. Only the engine
    side can raise here.
    """
    import httpx
    from x402_conformance_suite._engine import run_audit  # lazy import
    from api_server.visibility import run_directory_cold_probe
    from api_server.payment_required import decode_payment_required
    from api_server.batch_settlement import evaluate_batch_settlement_requirements

    report, (probe, snap) = await asyncio.gather(
        run_audit(url, timeout=timeout, mode=mode),
        run_directory_cold_probe(url, timeout),
    )

    payload = None
    http_status: int | None = None
    source = "none"

    if snap is not None and snap.status_code == 402:
        payload = decode_payment_required(body=snap.body, headers=snap.headers)
        http_status = 402
        if payload is not None:
            source = "cold_probe_post"

    if source == "none":
        # Single GET fallback; never raise — leave payload/source on error.
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                response = await client.get(url)
            http_status = response.status_code
            payload = decode_payment_required(
                body=response.text, headers=response.headers
            )
            source = "fallback_get"
        except Exception:  # noqa: BLE001 — never-raise contract for tools checks
            pass

    batch = evaluate_batch_settlement_requirements(
        payload,
        http_status=http_status,
        target_url=url,
        payload_source=source,
    )
    return report, probe, batch


def _require_api_key(x_api_key: str = Header(..., alias="X-API-Key")) -> str:
    """FastAPI dependency: 401 unless the supplied key is registered.

    Returns the API key itself so handlers can attribute usage and enforce
    the plan's monthly quota per key.
    """
    if not get_store().get(x_api_key):
        raise HTTPException(401, "Invalid API key")
    return x_api_key


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


# Match x402_conformance_suite._engine.auditor priority so tools-side checks
# (cold probe, later batch-settlement) affect overall the same way as engine checks.
_STATUS_PRIORITY: dict[str, int] = {
    "PASS": 0,
    "ERROR": 1,
    "FAIL": 2,
    "CRITICAL_FAIL": 3,
}


def _status_of(check) -> str:
    """Read status from a CheckResultItem or a plain dict entry."""
    if isinstance(check, dict):
        return str(check.get("status") or "ERROR")
    return str(getattr(check, "status", None) or "ERROR")


def _aggregate_check_results(checks: list) -> tuple[str, str]:
    """Recompute overall + summary after tools-side checks are appended.

    The engine's ``report.summary`` / ``report.overall_status`` only cover the
    suite checks. We append ``directory_cold_probe`` (and will append more)
    after the fact — without this recompute the API can report ``5/7`` while
    returning 8 entries, or overall PASS while a tools check FAILed.
    """
    if not checks:
        return "PASS", "0/0 checks passed. Overall: PASS"
    statuses = [_status_of(c) for c in checks]
    overall = max(statuses, key=lambda s: _STATUS_PRIORITY.get(s, 0))
    passed = sum(1 for s in statuses if s == "PASS")
    total = len(statuses)
    return overall, f"{passed}/{total} checks passed. Overall: {overall}"


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
<title>x402 Validator — strict-v2 conformance audits · Manifest, CAIP-2, JSON, Bazaar</title>
<meta name="description" content="Audit any x402 merchant endpoint for strict-v2 conformance in ~580 ms. Live demo, no signup · Free, Pro ($9/mo), Enterprise ($49/mo). Hosted by Gael L Chulim.">
<link rel="canonical" href="https://x402-validator-tools.onrender.com/">
<meta property="og:title" content="x402 Validator — strict-v2 conformance audits">
<meta property="og:description" content="Manifest, CAIP-2, JSON resilience, Bazaar compliance. Operator-actionable results in ~580 ms. Free demo + Pro API.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://x402-validator-tools.onrender.com/">
<meta property="og:site_name" content="x402 validator">
<meta property="og:image" content="https://x402-validator-tools.onrender.com/static/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="x402 Validator — strict-v2 conformance audits">
<meta name="twitter:description" content="Audit any x402 merchant in ~580 ms. Free demo + Pro API. Hosted on Render · Billed via Stripe.">
<meta name="twitter:image" content="https://x402-validator-tools.onrender.com/static/og-image.png">
<meta name="robots" content="index, follow">
<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
<link rel="icon" type="image/png" href="/static/favicon-32.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "SoftwareApplication",
      "name": "x402 Validator",
      "url": "https://x402-validator-tools.onrender.com/",
      "applicationCategory": "DeveloperApplication",
      "applicationSubCategory": "API Service / Testing Tool",
      "operatingSystem": "Any (hosted REST API)",
      "description": "Conformance audit API for x402 strict-v2 merchant endpoints. Runs Manifest, CAIP-2, JSON resilience, and Bazaar compliance checks against any URL and returns operator-actionable JSON.",
      "offers": [
        {"@type": "Offer", "name": "Free", "price": "0", "priceCurrency": "USD", "description": "100 audits / month · forever · no signup"},
        {"@type": "Offer", "name": "Pro", "price": "9", "priceCurrency": "USD", "description": "500 audits / month · API key · marketplace mode · email support"},
        {"@type": "Offer", "name": "Enterprise", "price": "49", "priceCurrency": "USD", "description": "5,000 audits / month · bulk · priority support · volume rebate"}
      ],
      "creator": {"@type": "Person", "name": "Gael L Chulim", "sameAs": "https://github.com/MSSATANASS"},
      "license": "https://www.apache.org/licenses/LICENSE-2.0"
    },
    {
      "@type": "FAQPage",
      "mainEntity": [
        {"@type": "Question", "name": "What is x402 conformance and why should I care?",
         "acceptedAnswer": {"@type": "Answer", "text": "x402 is the HTTP-402-based payment protocol from Coinbase. Strict-v2 conformance means your merchant endpoint serves a Bazaar-compliant manifest, advertises its CAIP-2 network/asset identifiers, returns resilient JSON, and exposes the 402 channel your buyers need. If any of those checks fail, gateways refuse to list you and customers see cryptic errors. This API runs all eight checks in ~580 ms and returns actionable operator errors."}},
        {"@type": "Question", "name": "What does the public demo actually check?",
         "acceptedAnswer": {"@type": "Answer", "text": "The same eight checks as /validate: manifest_discovery, caip2_compliance, json_resilience, bazaar_compliance, bot_wall, accepts_completeness, discovery_resource_listing, directory_cold_probe. Rate-limited to 3 audits per IP per day."}},
        {"@type": "Question", "name": "How long does an audit take?",
         "acceptedAnswer": {"@type": "Answer", "text": "Median ~580 ms end-to-end. Hits the endpoint, parses the response, runs all eight checks in parallel where independent."}},
        {"@type": "Question", "name": "Can I cancel a Pro / Enterprise plan?",
         "acceptedAnswer": {"@type": "Answer", "text": "Yes — cancel from your Stripe dashboard any time; you keep access until the end of the billing period."}},
        {"@type": "Question", "name": "What happens if my endpoint fails an audit?",
         "acceptedAnswer": {"@type": "Answer", "text": "The response includes the FAIL check name plus a message telling you what to fix. No log scraping, no email back-and-forth."}}
      ]
    }
  ]
}
</script>
<style>
/* ============================================================
   Design system: dark / black, two-font stack
   - Body:    Instrument Sans (Google Fonts)
   - Editorial: Instrument Serif for pre-headline serif accent
   - Accent:  #3054ff blue arrow on white pill; #b4c0ff gradient end
   ============================================================ */
:root {
  --bg: #F5F5F5;
  --fg: #0a0a0a;
  --fg-80: rgba(10,10,10,0.80);
  --fg-70: rgba(10,10,10,0.70);
  --fg-60: rgba(10,10,10,0.60);
  --fg-50: rgba(10,10,10,0.50);
  --accent: #0a0a0a;
  --accent-hover: #2b2644;
  --gradient-end: #2B2644;
  --primary-text-dark: #ffffff;
  --glass-border: rgba(10,10,10,0.10);
  --ink: #2B2644;
  --hero-grad: linear-gradient(to left, #2B2644, #FF4D00, #FF8A4D);
  --brand: #FF4D00;
  --brand-glow: rgba(255,77,0,0.30);
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #F5F5F5; }

body {
  font-family: 'Instrument Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;
  color: var(--fg);
  background: #F5F5F5;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  line-height: 1.5;
  overflow-x: hidden;
}

button { font-family: inherit; cursor: pointer; border: none; background: none; color: inherit; }
a { color: inherit; }


/* ============================================================
   Marquee (Halo-style infinite scroll)
   ============================================================ */
.marquee-wrap {
  width: 100%;
  max-width: 30rem;
  overflow: hidden;
  margin-top: 40px;
  -webkit-mask-image: linear-gradient(to right, transparent, #000 12%, #000 88%, transparent);
  mask-image: linear-gradient(to right, transparent, #000 12%, #000 88%, transparent);
}
.marquee-track {
  display: flex;
  width: max-content;
  animation: marquee 22s linear infinite;
}
@keyframes marquee {
  from { transform: translateX(0); }
  to   { transform: translateX(-50%); }
}
.marquee-item {
  margin: 0 28px;
  flex-shrink: 0;
  color: rgba(10,10,10,0.60);
  white-space: nowrap;
}
.stack-wrap {
  width: 100%;
  overflow: hidden;
  -webkit-mask-image: linear-gradient(to right, transparent, #000 8%, #000 92%, transparent);
  mask-image: linear-gradient(to right, transparent, #000 8%, #000 92%, transparent);
}
.stack-track {
  display: flex;
  width: max-content;
  animation: stack-marquee 30s linear infinite;
}
@keyframes stack-marquee {
  from { transform: translateX(0); }
  to   { transform: translateX(-50%); }
}
.stack-item {
  margin: 0 40px;
  flex-shrink: 0;
  color: rgba(10,10,10,0.50);
  white-space: nowrap;
}
.stack-section {
  padding: 56px 24px;
  border-top: 1px solid rgba(10,10,10,0.08);
  border-bottom: 1px solid rgba(10,10,10,0.08);
}
.stack-grid {
  max-width: 88rem; margin: 0 auto;
  display: grid; grid-template-columns: 1fr; gap: 28px; align-items: center;
}
@media (min-width: 768px) {
  .stack-grid { grid-template-columns: 1fr 3fr; gap: 32px; }
}
.stack-label {
  color: rgba(10,10,10,0.70);
  font-size: 0.95rem;
  line-height: 1.6;
  letter-spacing: -0.01em;
}

/* ============================================================
   Feature cards (Halo card grid)
   ============================================================ */
.cards-section { padding: 96px 24px; }
.cards-inner { max-width: 88rem; margin: 0 auto; }
.cards-top {
  display: grid; grid-template-columns: 1fr; gap: 40px;
  margin-bottom: 56px; align-items: start;
}
@media (min-width: 768px) { .cards-top { grid-template-columns: 1fr 1fr; } }
.cards-h2 {
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 600; font-size: 2.5rem; line-height: 1.1;
  letter-spacing: -0.03em; margin: 0 0 28px; color: var(--fg);
}
@media (min-width: 768px) { .cards-h2 { font-size: 3rem; } }
.cards-lede {
  color: rgba(10,10,10,0.70);
  font-size: 1.5rem; line-height: 1.5; letter-spacing: -0.02em; margin: 0;
}
@media (min-width: 768px) { .cards-lede { font-size: 1.75rem; } }
.card-grid {
  display: grid; grid-template-columns: 1fr; gap: 16px;
}
@media (min-width: 640px) { .card-grid { grid-template-columns: 1fr 1fr; } }
@media (min-width: 1024px) { .card-grid { grid-template-columns: repeat(4, 1fr); } }
.hcard {
  border-radius: 16px; padding: 28px; min-height: 20rem;
  display: flex; flex-direction: column; justify-content: space-between;
}
.hcard.wide { grid-column: span 1; }
@media (min-width: 1024px) { .hcard.wide { grid-column: span 2; } }
.hcard.light {
  background: #E4E4E4;
  background-image: radial-gradient(circle at 78% 18%, rgba(48,84,255,0.16), transparent 55%),
                    radial-gradient(circle at 12% 88%, rgba(43,38,68,0.14), transparent 50%);
}
.hcard.ink { background: var(--ink); }
.hcard-title {
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 600; font-size: 1.5rem; line-height: 1.25;
  letter-spacing: -0.02em; margin: 0; color: var(--fg);
}
.hcard.ink .hcard-title { color: #fff; }
.hcard-body {
  font-size: 1rem; line-height: 1.6; margin: 0; max-width: 22rem;
  color: rgba(10,10,10,0.70);
}
.hcard.ink .hcard-body { color: rgba(255,255,255,0.62); }
.hcard-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.78rem; letter-spacing: 0.04em; text-transform: uppercase;
  color: rgba(10,10,10,0.45); margin: 0 0 14px;
}
.hcard.ink .hcard-mono { color: rgba(255,255,255,0.45); }

/* ============================================================
   NAVBAR (fixed, transparent)
   ============================================================ */
.navbar {
  position: fixed; top: 0; left: 0; width: 100%;
  z-index: 50;
  background: transparent;
  padding: 16px 24px;
  display: flex; align-items: center; justify-content: space-between;
}
.nav-left, .nav-right { display: flex; align-items: center; gap: 20px; }
.nav-left .icon {
  width: 28px; height: 28px; display: block;
  border-radius: 7px;
  object-fit: cover;
}

.nav-links {
  display: none;
  align-items: center; gap: 32px;
}
.nav-links a {
  color: var(--fg-80); text-decoration: none;
  font-size: 14px; font-weight: 500;
  transition: color 0.15s;
  display: inline-flex; align-items: center; gap: 4px;
  letter-spacing: -0.005em;
}
.nav-links a:hover { color: var(--fg); }
.nav-links svg.chev { width: 14px; height: 14px; opacity: 0.7; }

.book-demo {
  display: none;
  color: var(--fg-80); font-size: 14px; font-weight: 500;
  text-decoration: none; transition: color 0.15s;
}
.book-demo:hover { color: var(--fg); }

.btn-primary-pill {
  background: var(--fg); color: var(--primary-text-dark);
  padding: 10px 20px;
  border-radius: 999px;
  font-size: 14px; font-weight: 600;
  text-decoration: none;
  letter-spacing: -0.01em;
  transition: box-shadow 0.2s, transform 0.15s;
  display: inline-block;
}
.btn-primary-pill:hover { box-shadow: 0 0 20px var(--brand-glow); }

@media (min-width: 640px) { .book-demo { display: inline-flex; } }
@media (min-width: 768px) { .nav-links { display: flex; } }

/* ============================================================
   HERO SECTION (full screen) — motion-style
   ============================================================ */
.hero {
  position: relative;
  width: calc(100% - 48px);
  max-width: 88rem;
  margin: 0 auto;
  min-height: calc(100vh - 96px);
  background: linear-gradient(165deg, #EFEDF7 0%, #EAF3EF 55%, #EDECF2 100%);
  color: var(--fg);
  overflow: hidden;
  border-radius: 24px;
  display: flex; flex-direction: column;
}
@media (max-width: 640px) {
  .hero { width: calc(100% - 24px); border-radius: 18px; }
}

.hero-video-wrap {
  position: absolute; inset: 0;
  z-index: 0;
  overflow: hidden;
}
/* --- Animated crypto background (violet + emerald, ~30% presence) --- */
.mesh {
  position: absolute;
  border-radius: 50%;
  filter: blur(90px);
  mix-blend-mode: multiply;
  will-change: transform;
}
.mesh-violet {
  width: 62%; height: 78%;
  top: -18%; right: -8%;
  background: radial-gradient(circle, rgba(255,77,0,0.22) 0%, rgba(255,77,0,0.08) 45%, transparent 70%);
  animation: drift-a 26s ease-in-out infinite;
}
.mesh-emerald {
  width: 55%; height: 70%;
  bottom: -22%; left: -10%;
  background: radial-gradient(circle, rgba(16,185,129,0.28) 0%, rgba(16,185,129,0.09) 45%, transparent 70%);
  animation: drift-b 32s ease-in-out infinite;
}
.mesh-deep {
  width: 48%; height: 55%;
  top: 30%; left: 32%;
  background: radial-gradient(circle, rgba(43,38,68,0.22) 0%, rgba(43,38,68,0.06) 50%, transparent 72%);
  animation: drift-c 38s ease-in-out infinite;
}
@keyframes drift-a {
  0%, 100% { transform: translate3d(0,0,0) scale(1); }
  50%      { transform: translate3d(-6%, 5%, 0) scale(1.10); }
}
@keyframes drift-b {
  0%, 100% { transform: translate3d(0,0,0) scale(1); }
  50%      { transform: translate3d(7%, -5%, 0) scale(1.08); }
}
@keyframes drift-c {
  0%, 100% { transform: translate3d(0,0,0) scale(1); }
  50%      { transform: translate3d(-4%, -6%, 0) scale(1.12); }
}

/* Ledger grid */
.hero-grid {
  position: absolute; inset: 0;
  background-image:
    linear-gradient(rgba(43,38,68,0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(43,38,68,0.055) 1px, transparent 1px);
  background-size: 64px 64px;
  -webkit-mask-image: radial-gradient(ellipse 75% 65% at 50% 45%, #000 35%, transparent 100%);
  mask-image: radial-gradient(ellipse 75% 65% at 50% 45%, #000 35%, transparent 100%);
}

/* Brand hexagon lattice (seamless 28x48 honeycomb tile, ~5% orange) */
.hero-video-wrap::after {
  content: '';
  position: absolute; inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='28' height='48' viewBox='0 0 28 48'%3E%3Cg fill='none' stroke='%23FF4D00' stroke-opacity='0.05' stroke-width='1.2'%3E%3Cpath d='M14 -4 28 4v16l-14 8L0 20V4Z'/%3E%3Cpath d='M0 20l14 8v16l-14 8-14-8V28Z'/%3E%3Cpath d='M28 20l14 8v16l-14 8-14-8V28Z'/%3E%3C/g%3E%3C/svg%3E");
  background-size: 28px 48px;
  -webkit-mask-image: radial-gradient(ellipse 80% 70% at 50% 42%, #000 30%, transparent 95%);
  mask-image: radial-gradient(ellipse 80% 70% at 50% 42%, #000 30%, transparent 95%);
}

/* Settlement flow lines */
.hero-flow {
  position: absolute; inset: 0;
  width: 100%; height: 100%;
  opacity: 0.30;
}
.flow-line {
  stroke-dasharray: 190 1400;
  stroke-linecap: round;
  animation: settle 9s linear infinite;
}
.flow-line.f2 { animation-duration: 12s; animation-delay: -3.5s; }
.flow-line.f3 { animation-duration: 15s; animation-delay: -7s; }
@keyframes settle {
  from { stroke-dashoffset: 1590; }
  to   { stroke-dashoffset: 0; }
}
.flow-nodes circle {
  fill: #2B2644;
  opacity: 0.35;
  animation: node-pulse 4s ease-in-out infinite;
}
.flow-nodes circle:nth-child(2) { animation-delay: -1.3s; }
.flow-nodes circle:nth-child(3) { animation-delay: -2.6s; }
.flow-nodes circle:nth-child(4) { animation-delay: -0.7s; }
@keyframes node-pulse {
  0%, 100% { opacity: 0.18; r: 3; }
  50%      { opacity: 0.55; r: 4.5; }
}

@media (prefers-reduced-motion: reduce) {
  .mesh, .flow-line, .flow-nodes circle { animation: none !important; }
}

.hero-overlay {
  position: absolute; inset: 0;
  background: linear-gradient(180deg, rgba(245,245,245,0.35) 0%, rgba(245,245,245,0.12) 45%, rgba(245,245,245,0.55) 100%);
  backdrop-filter: blur(1px);
  -webkit-backdrop-filter: blur(1px);
  z-index: 1;
}

.hero-decor {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  mix-blend-mode: multiply;
  pointer-events: none;
  z-index: 2;
}
.hero-decor.tl {
  top: -20%; left: 20%;
  width: 600px; height: 600px;
  background: rgba(255,77,0,0.16);  /* brand orange */
}
.hero-decor.br {
  bottom: -10%; right: 20%;
  width: 500px; height: 500px;
  background: rgba(16,185,129,0.20);  /* emerald-500/20 */
}

.hero-content {
  position: relative;
  z-index: 10;
  max-width: 88rem;
  margin: 0 auto;
  width: 100%;
  padding: 160px 48px 64px;
  text-align: left;
  display: flex; flex-direction: column; align-items: flex-start;
  gap: 20px;
}
@media (max-width: 640px) { .hero-content { padding: 120px 24px 48px; } }

/* ============================================================
   HeroCopy — pre-headline (serif), main (gradient), sub
   ============================================================ */
.brand-eyebrow {
  font-family: ui-monospace, SFMono-Regular, Menlo, 'Courier New', monospace;
  font-size: 0.95rem;
  letter-spacing: 0.14em;
  color: var(--fg-70);
  margin: 0;
}
.brand-eyebrow .brk { color: var(--brand); font-weight: 700; }

.pre-headline {
  font-family: 'Instrument Serif', 'Instrument Sans', serif;
  font-weight: 400;
  font-size: 1.875rem;  /* text-3xl mobile */
  line-height: 1.1;
  color: var(--fg);
  margin: 0;
  letter-spacing: -0.01em;
}

.main-headline {
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 600;
  font-size: 3.75rem;  /* text-6xl mobile */
  line-height: 0.9;
  letter-spacing: -0.05em;  /* tracking-tighter */
  margin: 0;
  background: linear-gradient(to bottom, #0a0a0a, #0a0a0a, #2B2644);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
  -webkit-text-fill-color: transparent;
}

.sub-headline {
  font-family: 'Instrument Sans', sans-serif;
  font-size: 1.125rem;  /* text-lg mobile */
  line-height: 1.65;
  color: var(--fg);
  opacity: 0.7;
  margin: 0;
  max-width: 34rem;
  letter-spacing: -0.005em;
}

@media (min-width: 640px) {
  .pre-headline { font-size: 3rem; }
  .main-headline { font-size: 6rem; }
  .sub-headline  { font-size: 1.25rem; }
}
@media (min-width: 1024px) {
  .pre-headline { font-size: 48px; }
  .main-headline { font-size: 136px; }
}

/* ============================================================
   Hero CTAs
   ============================================================ */
.hero-ctas {
  display: flex; flex-direction: column;
  align-items: flex-start; gap: 16px;
  margin-top: 16px;
}
@media (min-width: 640px) { .hero-ctas { flex-direction: row; } }

.cta-primary {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 8px 8px 8px 24px;
  background: var(--fg);
  border-radius: 999px;
  text-decoration: none;
  transition: transform 0.2s, box-shadow 0.2s;
}
.cta-primary:hover {
  transform: scale(1.05);
  box-shadow: 0 0 24px var(--brand-glow);
}
.cta-primary .label {
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 500;
  font-size: 1.125rem;
  color: var(--primary-text-dark);
  padding-right: 16px;
  letter-spacing: -0.01em;
}
.cta-primary .arrow {
  width: 40px; height: 40px;
  border-radius: 50%;
  background: var(--brand);
  display: inline-flex; align-items: center; justify-content: center;
  color: #fff;
  transition: background 0.2s, transform 0.2s;
}
.cta-primary:hover .arrow { background: #E64500; transform: translateX(2px); }
.cta-primary .arrow svg { width: 20px; height: 20px; }

.cta-secondary {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 16px;
  background: rgba(10,10,10,0.06);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-radius: 8px;
  color: var(--fg-70);
  text-decoration: none;
  font-family: 'Instrument Sans', sans-serif;
  font-size: 14px; font-weight: 500;
  transition: background 0.15s, color 0.15s;
  letter-spacing: -0.005em;
}
.cta-secondary:hover {
  background: rgba(10,10,10,0.05);
  color: var(--fg);
}
.cta-secondary svg {
  transition: transform 0.2s ease-out;
  width: 16px; height: 16px;
}
.cta-secondary:hover svg { transform: translateX(4px); }

/* ============================================================
   Below-hero (pricing & footer)
   ============================================================ */
.pricing-section {
  padding: 96px 24px;
  position: relative;
  max-width: 1100px;
  margin: 0 auto;
}
.pricing-pill {
  display: inline-block;
  padding: 6px 14px;
  background: rgba(10,10,10,0.04);
  border: 1px solid var(--glass-border);
  color: var(--fg-80);
  border-radius: 999px;
  font-size: 13px; font-weight: 500;
}
.pricing-eyebrow-row { display: flex; justify-content: center; margin-bottom: 16px; }
.pricing-headline {
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 600;
  font-size: clamp(1.875rem, 4vw, 3rem);
  letter-spacing: -0.025em;
  text-align: center;
  margin: 0 auto 24px;
  max-width: 32rem;
  line-height: 1.08;
}
.pricing-sub {
  text-align: center;
  color: var(--fg-70);
  max-width: 36rem;
  margin: 0 auto 48px;
  font-size: 1rem;
}

.pricing-grid {
  display: grid; gap: 20px;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  max-width: 1000px; margin: 0 auto;
}
.plan {
  background: rgba(10,10,10,0.04);
  border: 1px solid var(--glass-border);
  border-radius: 18px;
  padding: 32px 28px;
  display: flex; flex-direction: column;
  position: relative; overflow: hidden;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  transition: transform 0.2s, border-color 0.2s;
}
.plan:hover { transform: translateY(-3px); }
.plan.featured {
  border: 1px solid var(--accent);
  box-shadow: 0 16px 48px -16px rgba(255,77,0,0.30);
}
.plan.featured::before {
  content: "Most popular";
  position: absolute; top: 14px; right: -32px;
  background-image: var(--hero-grad);
  color: #000;
  padding: 4px 36px;
  font-size: 11px; font-weight: 700;
  transform: rotate(35deg);
  letter-spacing: 0.04em;
}
.plan-name { font-family: 'Instrument Sans', sans-serif; font-size: 1.1rem; font-weight: 700; margin: 0 0 6px; letter-spacing: -0.01em; }
.plan-desc { color: var(--fg-70); font-size: 0.875rem; margin: 0 0 24px; }
.plan-value { display: flex; align-items: baseline; gap: 6px; }
.plan-price { font-family: 'Instrument Sans', sans-serif; font-size: 3rem; font-weight: 700; line-height: 1; letter-spacing: -0.04em; }
.plan-period-small { font-size: 1rem; color: var(--fg-70); font-weight: 400; }
.plan-period-line { font-size: 0.8rem; color: var(--fg-60); margin: 6px 0 0; }

.plan-features { list-style: none; padding: 0; margin: 24px 0 0; flex-grow: 1; }
.plan-features li {
  padding: 10px 0;
  display: flex; align-items: center; gap: 10px;
  font-size: 0.92rem;
  border-top: 1px solid rgba(10,10,10,0.08);
}
.plan-features li:last-child { border-bottom: 1px solid rgba(10,10,10,0.08); }
.plan-features li svg { flex-shrink: 0; color: #047857; width: 16px; height: 16px; }

.plan-cta {
  width: 100%; padding: 12px 24px; margin-top: 24px;
  border-radius: 999px;
  font-family: 'Instrument Sans', sans-serif;
  font-size: 0.95rem; font-weight: 600;
  border: none; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
  text-decoration: none;
  transition: transform 0.15s, box-shadow 0.15s;
  letter-spacing: -0.01em;
}
.plan-cta:hover { transform: translateY(-1px); }
.plan-cta.solid { background: var(--fg); color: var(--primary-text-dark); }
.plan-cta.outline { background: transparent; color: var(--fg); border: 1px solid rgba(10,10,10,0.15); }

/* ============================================================
   Footer
   ============================================================ */
footer {
  border-top: 1px solid rgba(10,10,10,0.08);
  padding: 64px 24px 80px;
  color: var(--fg-70);
}
.foot-grid {
  display: grid; gap: 32px;
  grid-template-columns: 1.4fr repeat(3, 1fr);
  max-width: 1100px; margin: 0 auto;
}
@media (max-width: 760px) { .foot-grid { grid-template-columns: 1fr 1fr; } }
.foot-brand .mark-hex {
  width: 40px; height: 40px; display: block;
  border-radius: 9px;
  object-fit: cover;
}
.foot-brand .logo-wordmark {
  display: block;
  width: min(240px, 100%);
  height: auto;
  border-radius: 10px;
  margin-bottom: 4px;
}
.foot-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, 'Courier New', monospace;
  font-size: 0.78rem; letter-spacing: 0.08em;
  color: var(--fg-50); margin: 2px 0 8px;
}
.foot-mono .brk { color: var(--brand); font-weight: 700; }
.foot-brand .name { font-weight: 700; font-size: 1rem; margin: 10px 0 6px; color: var(--fg); font-family: 'Instrument Sans', sans-serif; }
.foot-brand .tag { font-size: 0.875rem; max-width: 280px; color: var(--fg-50); }

.foot-col h4 {
  font-size: 0.72rem; color: var(--fg-50);
  text-transform: uppercase; letter-spacing: 0.06em;
  margin: 0 0 14px; font-weight: 600;
  font-family: 'Instrument Sans', sans-serif;
}
.foot-col a {
  display: block; color: var(--fg-80);
  text-decoration: none; font-size: 0.875rem;
  padding: 4px 0;
  transition: color 0.15s;
}
.foot-col a:hover { color: var(--fg); }
.foot-bottom {
  border-top: 1px solid rgba(10,10,10,0.08);
  margin: 40px auto 0; padding-top: 20px;
  max-width: 1100px;
  font-size: 0.8rem; color: var(--fg-50);
  display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px;
}
.foot-bottom code { color: var(--fg-80); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

/* ============================================================
   AUDIT DEMO section
   ============================================================ */
.audit-demo-section {
  padding: 96px 24px 80px;
  position: relative;
  max-width: 1000px;
  margin: 0 auto;
  border-top: 1px solid rgba(10,10,10,0.06);
}
.audit-demo-headline {
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 600;
  font-size: clamp(1.875rem, 4vw, 2.75rem);
  letter-spacing: -0.025em;
  text-align: center;
  margin: 12px auto 16px;
  max-width: 36rem;
  line-height: 1.08;
  color: var(--fg);
}
.audit-demo-sub {
  text-align: center;
  color: var(--fg-70);
  max-width: 38rem;
  margin: 0 auto 32px;
  font-size: 1rem;
  line-height: 1.55;
}
.audit-demo-sub strong { color: var(--fg); font-weight: 600; }

.audit-form {
  background: rgba(10,10,10,0.04);
  border: 1px solid rgba(10,10,10,0.10);
  border-radius: 16px;
  padding: 20px 20px 14px;
  max-width: 760px;
  margin: 0 auto;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.audit-input-row {
  display: flex; flex-direction: column; gap: 10px;
  align-items: stretch;
}
@media (min-width: 700px) {
  .audit-input-row { flex-direction: row; align-items: center; gap: 8px; }
}
.audit-form input[type="url"] {
  flex: 1;
  background: #ffffff;
  border: 1px solid rgba(10,10,10,0.14);
  border-radius: 10px;
  padding: 12px 14px;
  color: var(--fg);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.95rem;
  outline: none;
  transition: border-color 0.15s;
  min-width: 0;
}
.audit-form input[type="url"]:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px rgba(255,77,0,0.15);
}
.audit-form select {
  background: #ffffff;
  border: 1px solid rgba(10,10,10,0.14);
  border-radius: 10px;
  padding: 12px 14px;
  color: var(--fg);
  font-family: 'Instrument Sans', sans-serif;
  font-size: 0.95rem;
  cursor: pointer;
  outline: none;
}
.audit-submit {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 12px 22px;
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 600;
  font-size: 0.95rem;
  cursor: pointer;
  transition: background 0.15s, transform 0.1s;
}
.audit-submit:hover { background: var(--brand); }
.audit-submit:active { transform: scale(0.98); }
.audit-submit[disabled] { opacity: 0.6; cursor: progress; }

.audit-hint {
  font-size: 0.78rem;
  color: var(--fg-50);
  margin: 10px 4px 0;
  text-align: center;
}
.audit-hint a { color: var(--accent); text-decoration: none; }
.audit-hint a:hover { text-decoration: underline; }

.audit-results {
  max-width: 760px;
  margin: 20px auto 0;
}
.audit-summary {
  background: #ffffff;
  border: 1px solid rgba(10,10,10,0.10);
  border-radius: 12px;
  padding: 14px 18px;
  font-family: 'Instrument Sans', sans-serif;
  margin-bottom: 10px;
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.audit-summary.overall-PASS { border-color: rgba(16,185,129,0.4); }
.audit-summary.overall-FAIL { border-color: rgba(239,68,68,0.4); }
.audit-summary strong { color: var(--fg); font-weight: 700; }
.audit-summary .badge {
  display: inline-block; padding: 4px 10px;
  border-radius: 999px;
  font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.06em; text-transform: uppercase;
}
.audit-summary .badge.PASS { background: rgba(5,150,105,0.14); color: #047857; }
.audit-summary .badge.FAIL { background: rgba(220,38,38,0.12); color: #b91c1c; }
.audit-summary .latency { color: var(--fg-50); font-size: 0.85rem; margin-left: auto; }

.check-row {
  background: #ffffff;
  border: 1px solid rgba(10,10,10,0.06);
  border-radius: 10px;
  padding: 12px 14px;
  display: grid;
  grid-template-columns: 72px 1fr;
  gap: 12px;
  margin-bottom: 6px;
  font-family: 'Instrument Sans', sans-serif;
  font-size: 0.92rem;
}
.check-row.status-PASS { border-color: rgba(16,185,129,0.25); }
.check-row.status-FAIL { border-color: rgba(239,68,68,0.25); }
.check-row .check-status {
  align-self: start;
  font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.06em;
  padding: 4px 0; text-align: center; text-transform: uppercase;
}
.check-row .check-status.PASS { color: #047857; }
.check-row .check-status.FAIL { color: #b91c1c; }
.check-row .check-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--fg); font-weight: 600;
  display: block; margin-bottom: 2px;
}
.check-row .check-msg { color: var(--fg-70); font-size: 0.86rem; line-height: 1.55; }

.audit-error, .audit-rate {
  background: rgba(239,68,68,0.08);
  border: 1px solid rgba(239,68,68,0.30);
  border-radius: 12px;
  padding: 14px 16px;
  font-family: 'Instrument Sans', sans-serif;
  font-size: 0.92rem;
  line-height: 1.55;
}
.audit-rate { background: rgba(251,191,36,0.08); border-color: rgba(251,191,36,0.30); }
.audit-rate a { color: var(--accent); text-decoration: none; font-weight: 600; }
.audit-rate a:hover { text-decoration: underline; }
.audit-loading {
  background: rgba(10,10,10,0.03);
  border: 1px solid rgba(10,10,10,0.08);
  border-radius: 10px;
  padding: 14px 16px;
  font-family: 'Instrument Sans', sans-serif;
  font-size: 0.92rem;
  color: var(--fg-70);
  display: flex; align-items: center; gap: 10px;
}
.audit-loading .spinner {
  width: 14px; height: 14px;
  border: 2px solid rgba(10,10,10,0.18);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* ============================================================
   FAQ section
   ============================================================ */
.faq-section {
  padding: 96px 24px 80px;
  max-width: 800px;
  margin: 0 auto;
  border-top: 1px solid rgba(10,10,10,0.06);
}
.faq-headline {
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 600;
  font-size: clamp(1.875rem, 4vw, 2.5rem);
  letter-spacing: -0.025em;
  text-align: center;
  margin: 0 0 32px;
  line-height: 1.08;
  color: var(--fg);
}
.faq-list { display: flex; flex-direction: column; gap: 10px; }
.faq-item {
  background: rgba(10,10,10,0.03);
  border: 1px solid rgba(10,10,10,0.08);
  border-radius: 12px;
  padding: 0;
  transition: border-color 0.15s;
}
.faq-item:hover { border-color: rgba(10,10,10,0.14); }
.faq-item summary {
  cursor: pointer;
  padding: 18px 20px;
  font-family: 'Instrument Sans', sans-serif;
  font-weight: 600;
  font-size: 1.02rem;
  color: var(--fg);
  list-style: none;
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px;
}
.faq-item summary::-webkit-details-marker { display: none; }
.faq-item summary::after {
  content: '+';
  font-size: 1.4rem;
  font-weight: 300;
  color: var(--fg-50);
  transition: transform 0.2s;
  flex-shrink: 0;
}
.faq-item[open] summary::after { content: '−'; color: var(--brand); }
.faq-item p {
  padding: 0 20px 18px;
  margin: 0;
  color: var(--fg-70);
  font-size: 0.95rem;
  line-height: 1.65;
}
.faq-item p code {
  background: rgba(10,10,10,0.06);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 0.85rem;
  color: var(--fg);
}

/* ============================================================
   Animations (pure CSS, motion-style timing)
   ============================================================ */
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(20px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.9); }
  to   { opacity: 1; transform: scale(1); }
}
@keyframes fade70 {
  from { opacity: 0; }
  to   { opacity: 0.7; }
}
.anim-fade-up       { animation: fadeUp 0.6s ease-out both; }
.anim-scale         { animation: scaleIn 0.6s ease-out 0.2s both; }
.anim-fade70        { animation: fade70 0.6s ease-out 0.4s both; }
.anim-fade-up-late  { animation: fadeUp 0.5s ease-out 0.6s both; }

@media (max-width: 600px) {
  .navbar { padding: 12px 18px; }
  .pricing-section { padding: 64px 20px; }
  .hero-content { padding-top: 100px; }
}
</style>
</head>
<body>

<!-- =================== NAVBAR (fixed, transparent) =================== -->
<nav class="navbar">
  <div class="nav-left">
    <a href="/" style="display:flex;align-items:center;gap:10px;text-decoration:none;">
      <img class="icon" src="/static/logo-mark-512.png" alt="x402 validator" width="28" height="28">
      <span style="color:#0a0a0a;font-family:'Instrument Sans',sans-serif;font-weight:700;font-size:15px;letter-spacing:-0.01em;">x402 validator</span>
    </a>
  </div>

  <div class="nav-links">
    <a href="#audit">Try It Free</a>
    <a href="#pricing">Pricing</a>
    <a href="/vs-x402-doctor">Compare</a>
    <a href="/open">Open</a>
    <a href="#faq">FAQ</a>
    <a href="/health">Status</a>
  </div>

  <div class="nav-right">
    __AUTH_NAV__
    <a class="book-demo" href="https://github.com/MSSATANASS/x402-validator-tools/issues">Contact</a>
    <a class="btn-primary-pill" href="/create-checkout-session?plan_id=pro">Get Started</a>
  </div>
</nav>

<!-- =================== HERO SECTION (motion-style) =================== -->
<section class="hero">
  <!-- Animated crypto background: mesh gradients + ledger grid + settlement flow.
       Pure CSS/SVG — no video, no CDN dependency, ~30% presence. -->
  <div class="hero-video-wrap" aria-hidden="true">
    <div class="mesh mesh-violet"></div>
    <div class="mesh mesh-emerald"></div>
    <div class="mesh mesh-deep"></div>
    <div class="hero-grid"></div>
    <svg class="hero-flow" viewBox="0 0 1200 600" preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id="flowViolet" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#FF4D00" stop-opacity="0"/>
          <stop offset="50%" stop-color="#FF4D00" stop-opacity="0.9"/>
          <stop offset="100%" stop-color="#FF4D00" stop-opacity="0"/>
        </linearGradient>
        <linearGradient id="flowEmerald" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="#10B981" stop-opacity="0"/>
          <stop offset="50%" stop-color="#10B981" stop-opacity="0.9"/>
          <stop offset="100%" stop-color="#10B981" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path class="flow-line f1" d="M-100 170 H 400 Q 470 170 470 240 V 330 Q 470 400 540 400 H 1300" fill="none" stroke="url(#flowViolet)" stroke-width="1.5"/>
      <path class="flow-line f2" d="M-100 430 H 300 Q 370 430 370 360 V 250 Q 370 180 440 180 H 1300" fill="none" stroke="url(#flowEmerald)" stroke-width="1.5"/>
      <path class="flow-line f3" d="M-100 300 H 700 Q 780 300 780 220 V 140 Q 780 70 850 70 H 1300" fill="none" stroke="url(#flowViolet)" stroke-width="1.2"/>
      <g class="flow-nodes">
        <circle cx="470" cy="240" r="3.5"/><circle cx="370" cy="360" r="3.5"/>
        <circle cx="780" cy="220" r="3"/><circle cx="540" cy="400" r="3"/>
      </g>
    </svg>
  </div>
  <div class="hero-overlay"></div>
  <div class="hero-decor tl"></div>
  <div class="hero-decor br"></div>

  <div class="hero-content">
    <p class="brand-eyebrow anim-fade-up"><span class="brk">[</span> x402-validator-tools <span class="brk">]</span></p>
    <p class="pre-headline anim-fade-up">Ship x402 endpoints with confidence</p>
    <h1 class="main-headline anim-scale">Audit x402 in Seconds</h1>
    <p class="sub-headline">Manifest, CAIP-2, JSON resilience, and Bazaar compliance — checked against any merchant URL in ~580 ms, with operator-actionable errors.</p>
    <div class="hero-ctas anim-fade-up-late">
      <a class="cta-primary" href="/create-checkout-session?plan_id=pro">
        <span class="label">Get Your API Key</span>
        <span class="arrow">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
        </span>
      </a>
      <a class="cta-secondary" href="#audit">
        Try It Free
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
      </a>
    </div>

    <div class="marquee-wrap" aria-hidden="true">
      <div class="marquee-track">
        <span class="marquee-item" style="font-family:Georgia,serif;font-weight:700;letter-spacing:-0.02em;font-size:15px;">manifest_discovery</span>
        <span class="marquee-item" style="font-family:Arial,sans-serif;font-weight:900;letter-spacing:0.08em;font-size:13px;text-transform:uppercase;">caip2</span>
        <span class="marquee-item" style="font-family:'Trebuchet MS',sans-serif;font-weight:600;letter-spacing:0.01em;font-size:15px;font-style:italic;">json_resilience</span>
        <span class="marquee-item" style="font-family:'Courier New',monospace;font-weight:700;letter-spacing:0.12em;font-size:13px;text-transform:uppercase;">bazaar</span>
        <span class="marquee-item" style="font-family:Palatino,'Book Antiqua',serif;font-weight:400;letter-spacing:-0.01em;font-size:16px;">bot_wall</span>
        <span class="marquee-item" style="font-family:Impact,'Arial Narrow',sans-serif;font-weight:400;letter-spacing:0.04em;font-size:14px;">accepts[]</span>
        <span class="marquee-item" style="font-family:Verdana,sans-serif;font-weight:700;letter-spacing:-0.03em;font-size:13px;">discovery_listing</span>
        <span class="marquee-item" style="font-family:Palatino,'Book Antiqua',serif;font-weight:400;letter-spacing:-0.01em;font-size:16px;font-style:italic;">cold_probe</span>
        <span class="marquee-item" style="font-family:Georgia,serif;font-weight:700;letter-spacing:-0.02em;font-size:15px;">manifest_discovery</span>
        <span class="marquee-item" style="font-family:Arial,sans-serif;font-weight:900;letter-spacing:0.08em;font-size:13px;text-transform:uppercase;">caip2</span>
        <span class="marquee-item" style="font-family:'Trebuchet MS',sans-serif;font-weight:600;letter-spacing:0.01em;font-size:15px;font-style:italic;">json_resilience</span>
        <span class="marquee-item" style="font-family:'Courier New',monospace;font-weight:700;letter-spacing:0.12em;font-size:13px;text-transform:uppercase;">bazaar</span>
        <span class="marquee-item" style="font-family:Palatino,'Book Antiqua',serif;font-weight:400;letter-spacing:-0.01em;font-size:16px;">bot_wall</span>
        <span class="marquee-item" style="font-family:Impact,'Arial Narrow',sans-serif;font-weight:400;letter-spacing:0.04em;font-size:14px;">accepts[]</span>
        <span class="marquee-item" style="font-family:Verdana,sans-serif;font-weight:700;letter-spacing:-0.03em;font-size:13px;">discovery_listing</span>
        <span class="marquee-item" style="font-family:Palatino,'Book Antiqua',serif;font-weight:400;letter-spacing:-0.01em;font-size:16px;font-style:italic;">cold_probe</span>
      </div>
    </div>
  </div>
</section>

<!-- =================== MEET THE ENGINE (Halo-style card grid) =================== -->
<section class="cards-section">
  <div class="cards-inner">
    <div class="cards-top">
      <div>
        <h2 class="cards-h2">Meet the engine.</h2>
        <a class="cta-primary" href="/open">
          <span class="label">See real numbers</span>
          <span class="arrow">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
          </span>
        </a>
      </div>
      <p class="cards-lede">An open-source conformance engine that tells you whether an x402 endpoint is actually payable by an agent &mdash; not just whether it responds.</p>
    </div>

    <div class="card-grid">
      <div class="hcard light wide">
        <div>
          <p class="hcard-mono">8 checks &middot; standard mode</p>
          <h3 class="hcard-title">Depth, not a ping</h3>
        </div>
        <p class="hcard-body">Manifest, CAIP-2 inside v2 <code>accepts[]</code>, JSON resilience, Bazaar shape, bot-wall detection, atomic-unit amounts, catalog listing, and directory cold-probe visibility &mdash; each with an operator-actionable message.</p>
      </div>

      <div class="hcard ink">
        <div>
          <p class="hcard-mono">Marketplace mode</p>
          <h3 class="hcard-title">Every product,<br/>not just the root</h3>
        </div>
        <p class="hcard-body">A free catalog page shouldn't fail your audit. We walk each paid product's own 402 and validate it independently.</p>
      </div>

      <div class="hcard ink">
        <div>
          <p class="hcard-mono">Apache-2.0</p>
          <h3 class="hcard-title">Runs where<br/>you work</h3>
        </div>
        <p class="hcard-body">PyPI library, MCP server, GitHub Action, batch CLI, and this API &mdash; the same engine, 203 tests behind it.</p>
      </div>
    </div>
  </div>
</section>

<!-- =================== STACK MARQUEE =================== -->
<section class="stack-section">
  <div class="stack-grid">
    <p class="stack-label">Open source engine,<br/>shipped on boring infrastructure.</p>
    <div class="stack-wrap" aria-hidden="true">
      <div class="stack-track">
        <span class="stack-item" style="font-family:'Times New Roman',serif;font-weight:400;letter-spacing:0.02em;font-size:14px;">Apache-2.0</span>
        <span class="stack-item" style="font-family:'Arial Black',sans-serif;font-weight:900;letter-spacing:0.08em;font-size:16px;">PyPI</span>
        <span class="stack-item" style="font-family:Impact,sans-serif;font-weight:700;letter-spacing:0.05em;font-size:18px;">MCP</span>
        <span class="stack-item" style="font-family:Georgia,serif;font-weight:600;letter-spacing:-0.02em;font-size:17px;">GitHub Action</span>
        <span class="stack-item" style="font-family:Helvetica,sans-serif;font-weight:700;letter-spacing:-0.01em;font-size:15px;">FastAPI</span>
        <span class="stack-item" style="font-family:Verdana,sans-serif;font-weight:700;letter-spacing:0.06em;font-size:14px;text-transform:uppercase;">Render</span>
        <span class="stack-item" style="font-family:'Courier New',monospace;font-weight:700;letter-spacing:0.18em;font-size:14px;">Stripe</span>
        <span class="stack-item" style="font-family:Palatino,serif;font-weight:500;letter-spacing:0.03em;font-size:15px;">203 tests</span>
        <span class="stack-item" style="font-family:'Times New Roman',serif;font-weight:400;letter-spacing:0.02em;font-size:14px;">Apache-2.0</span>
        <span class="stack-item" style="font-family:'Arial Black',sans-serif;font-weight:900;letter-spacing:0.08em;font-size:16px;">PyPI</span>
        <span class="stack-item" style="font-family:Impact,sans-serif;font-weight:700;letter-spacing:0.05em;font-size:18px;">MCP</span>
        <span class="stack-item" style="font-family:Georgia,serif;font-weight:600;letter-spacing:-0.02em;font-size:17px;">GitHub Action</span>
        <span class="stack-item" style="font-family:Helvetica,sans-serif;font-weight:700;letter-spacing:-0.01em;font-size:15px;">FastAPI</span>
        <span class="stack-item" style="font-family:Verdana,sans-serif;font-weight:700;letter-spacing:0.06em;font-size:14px;text-transform:uppercase;">Render</span>
        <span class="stack-item" style="font-family:'Courier New',monospace;font-weight:700;letter-spacing:0.18em;font-size:14px;">Stripe</span>
        <span class="stack-item" style="font-family:Palatino,serif;font-weight:500;letter-spacing:0.03em;font-size:15px;">203 tests</span>
      </div>
    </div>
  </div>
</section>

<!-- =================== AUDIT DEMO (interactive, free, rate-limited) =================== -->
<section id="audit" class="audit-demo-section">
  <div class="pricing-eyebrow-row"><span class="pricing-pill">Live demo · No signup</span></div>
  <h2 class="audit-demo-headline">Audit an x402 endpoint right now</h2>
  <p class="audit-demo-sub">Paste any merchant URL. We run eight checks against it: Manifest, CAIP-2, JSON resilience, Bazaar compliance, bot-wall detection, accepts[] completeness, discovery listing, and directory visibility &mdash; the Bazaar cold probe. No API key, no signup. <strong>3 audits per IP per day</strong> on the public demo.</p>

  <!-- action="/method=get" keep the form well-formed; the inline onsubmit guard
       returns false until the bound JS handler flips window.__x402AuditReady
       to true, so a degraded submit NEVER silently resets the page. -->
  <form id="auditForm" class="audit-form" autocomplete="off"
        action="/" method="get"
        onsubmit="if(window.__x402AuditReady!==true){var n=document.getElementById('auditInlineErr');if(!n){n=document.createElement('div');n.id='auditInlineErr';n.className='audit-error';n.style.cssText='margin-top:10px;font-size:0.85rem;';n.textContent='Page scripts did not load (network error or stale cache). Please refresh (Ctrl+R / Cmd+R) and try again. If this persists, contact support.';this.appendChild(n);}n.hidden=false;}return window.__x402AuditReady===true;">
    <div class="audit-input-row">
      <input type="url" id="auditUrl" name="url" required
             value="https://observer.137-184-67-179.sslip.io"
             placeholder="https://your-merchant.com"
             aria-label="x402 merchant URL to audit" />
      <select id="auditMode" name="mode" aria-label="audit mode">
        <option value="standard">Standard</option>
        <option value="marketplace">Marketplace</option>
      </select>
      <button type="submit" class="audit-submit">Audit free</button>
    </div>
    <p class="audit-hint" id="auditHint">No URL handy? <a href="#" onclick="fillUrl('https://observer.137-184-67-179.sslip.io');return false">Load a live x402 endpoint</a> and audit it.</p>
    <noscript>
      <p style="margin-top:10px;color:var(--fg-50);font-size:0.85rem;">The audit demo requires JavaScript. <a href="/health">Check status</a> or enable scripts and refresh.</p>
    </noscript>
  </form>

  <div id="auditResults" class="audit-results" aria-live="polite" hidden></div>
</section>

<!-- =================== PRICING (kept, restyled to dark theme) =================== -->
<section id="pricing" class="pricing-section">
  <div class="pricing-eyebrow-row"><span class="pricing-pill">Pricing</span></div>
  <h2 class="pricing-headline">Simple pricing. <br/>Cancel anytime.</h2>
  <p class="pricing-sub">Stripe-billed. No long-term contract. Cancel from your dashboard anytime.</p>

  <div class="pricing-grid">
    <div class="plan">
      <h3 class="plan-name">Free</h3>
      <p class="plan-desc">For trying it out on a single merchant.</p>
      <div class="plan-value"><div class="plan-price">$0</div><span class="plan-period-small">/mo</span></div>
      <div class="plan-period-line">100 audits / month · forever</div>
      <ul class="plan-features">
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>All 7 conformance checks</li>
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>JSON response</li>
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Community support</li>
      </ul>
      <a class="plan-cta outline" href="/create-checkout-session?plan_id=free">Start free</a>
    </div>

    <div class="plan featured">
      <h3 class="plan-name">Pro</h3>
      <p class="plan-desc">For shipping x402 merchants.</p>
      <div class="plan-value"><div class="plan-price">$9</div><span class="plan-period-small">/mo</span></div>
      <div class="plan-period-line">500 audits / month</div>
      <ul class="plan-features">
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Everything in Free</li>
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>marketplace mode</li>
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Operator-actionable errors</li>
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Email support</li>
      </ul>
      <a class="plan-cta solid" href="/create-checkout-session?plan_id=pro">Buy Pro — $9 / mo</a>
    </div>

    <div class="plan">
      <h3 class="plan-name">Enterprise</h3>
      <p class="plan-desc">For higher-volume catalog compliance.</p>
      <div class="plan-value"><div class="plan-price">$49</div><span class="plan-period-small">/mo</span></div>
      <div class="plan-period-line">5,000 audits / month</div>
      <ul class="plan-features">
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Everything in Pro</li>
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Bulk (beta)</li>
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Priority support</li>
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Volume rebate</li>
      </ul>
      <a class="plan-cta outline" href="/create-checkout-session?plan_id=enterprise">Buy Enterprise — $49 / mo</a>
    </div>
  </div>
</section>

<!-- =================== FAQ =================== -->
<section id="faq" class="faq-section">
  <h2 class="faq-headline">Frequently asked questions</h2>
  <div class="faq-list">
    <details class="faq-item">
      <summary>What is x402 conformance and why should I care?</summary>
      <p>x402 is the HTTP-402-based payment protocol from Coinbase. Strict-v2 conformance means your merchant endpoint serves a Bazaar-compliant manifest, advertises its CAIP-2 network/asset identifiers, returns resilient JSON, and exposes the 402 channel your buyers need. If any of those checks fail, gateways refuse to list you and customers see cryptic errors. This API runs all eight checks in ~580 ms and returns actionable operator errors.</p>
    </details>
    <details class="faq-item">
      <summary>What does the public demo actually check?</summary>
      <p>The same eight checks as <code>/validate</code>: <code>manifest_discovery</code>, <code>caip2_compliance</code>, <code>json_resilience</code>, <code>bazaar_compliance</code>, <code>bot_wall</code>, <code>accepts_completeness</code>, <code>discovery_resource_listing</code>, <code>directory_cold_probe</code>. The demo is rate-limited to 3 audits per IP per day — that's enough to convince you, not enough to abuse. Buy a Pro key for 500 audits/month; Enterprise gets you 5,000.</p>
    </details>
    <details class="faq-item">
      <summary>Is the public demo really free? What about my data?</summary>
      <p>Yes, the demo is free and requires no signup. We log the URL you submit and your IP only for abuse detection (matching the rate limit). We do not sell, share, or persist the audit results anywhere. Buy a key and the same engine runs against your merchant endpoints; results are returned to you only.</p>
    </details>
    <details class="faq-item">
      <summary>How long does an audit take?</summary>
      <p>Median <strong>~580 ms</strong> end-to-end. We hit your endpoint, parse the response, run all eight checks in parallel where independent, and return structured JSON. Failing checks ship with operator-actionable messages, not stack traces.</p>
    </details>
    <details class="faq-item">
      <summary>Can I cancel a Pro / Enterprise plan?</summary>
      <p>Yes — cancel from your Stripe dashboard any time; you keep access until the end of the billing period. We do not lock you in. Refunds for the current cycle are handled per Stripe's standard subscription refund policy; contact support for special cases.</p>
    </details>
    <details class="faq-item">
      <summary>What happens if my endpoint fails an audit?</summary>
      <p>The response includes the <code>FAIL</code> check name plus a message telling you what to fix. Example: <code>"Payment-Required header missing"</code> for the CAIP-2 check. No log scraping, no email back-and-forth — just paste the output into your team's channel.</p>
    </details>
    <details class="faq-item">
      <summary>Who runs this?</summary>
      <p>x402 validator is built and operated by Gael L Chulim (<a href="https://github.com/MSSATANASS">GitHub: MSSATANASS</a>). The engine is Apache-2.0 and open source (<a href="https://github.com/MSSATANASS/x402-conformance-engine" rel="noopener">GitHub</a>); the audit API is a hosted service on Render and billed through Stripe.</p>
    </details>
  </div>
</section>

<!-- =================== FOOTER =================== -->
<footer>
  <div class="foot-grid">
    <div class="foot-brand">
      <img class="logo-wordmark" src="/static/logo-wordmark.png" alt="x402 validator tools" width="240" height="106">
      <div class="tag">REST API that runs the x402 strict-v2 conformance suite against any URL.</div>
    </div>
    <div class="foot-col">
      <h4>Product</h4>
      <a href="#pricing">Pricing</a>
      <a href="/vs-x402-doctor">vs x402 Doctor</a>
      <a href="/open">Open metrics</a>
      <a href="/plans">Plans API</a>
      <a href="/health">Status</a>
      <a href="https://github.com/MSSATANASS/x402-validator-tools/issues">Support</a>
    </div>
    <div class="foot-col">
      <h4>Code</h4>
      <a href="https://github.com/MSSATANASS/x402-validator-tools" rel="noopener">Tools (this site)</a>
      <a href="https://github.com/MSSATANASS/x402-conformance-engine" rel="noopener">Engine fork</a>
      <a href="https://github.com/smartflowproai-lang/x402-endpoint-validator" rel="noopener">Upstream</a>
      <a href="https://pypi.org/project/x402-conformance-suite/" rel="noopener">pip install</a>
    </div>
    <div class="foot-col">
      <h4>Contact</h4>
      <a href="https://github.com/MSSATANASS/x402-validator-tools/issues">GitHub Issues</a>
      <a href="https://github.com/MSSATANASS/x402-validator-tools/issues" rel="noopener">GitHub issues</a>
      <a href="https://github.com/MSSATANASS">GitHub: MSSATANASS</a>
    </div>
  </div>
  <div class="foot-bottom">
    <div>© 2026 x402 validator · Apache-2.0</div>
    <div>stripe • persistent key store in <code>api_keys.json</code></div>
  </div>
</footer>

<script>
// Hero background is pure CSS/SVG (mesh gradients + ledger grid + flow lines).
// No video element, no HLS, no external media dependency.
//
// Audit-demo form handler. Kept in a single IIFE: the previous version had a
// dangling outer `(function () {` that broke the whole block as a SyntaxError,
// which meant the submit handler never bound and every form submit fell
// through to the browser's native GET (resetting the page silently). See
// ROBUSTNESS.md / commit history for the bug report from Ali Nain / Viridis.
(function () {
  // Safety flag read by the inline onsubmit guard on <form id="auditForm">.
  // Until bind() finishes, submitting the form is refused at the kernel level
  // and shows an inline error instead of resetting the page.
  window.__x402AuditReady = false;

  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderResults(el, body, status) {
    if (!el) return;
    if (status === 429) {
      el.innerHTML =
        '<div class="audit-rate">Daily limit reached (3 per IP). ' +
        'Get unlimited audits with Pro — ' +
        '<a href="/create-checkout-session?plan_id=pro">buy Pro ($9/mo)</a>.</div>';
      return;
    }
    if (!body || body.detail) {
      el.innerHTML =
        '<div class="audit-error">' + esc(body && body.detail || 'Audit failed') + '</div>';
      return;
    }
    var checksHTML = (body.checks || []).map(function (c) {
      return '<div class="check-row status-' + esc(c.status) + '">' +
             '<span class="check-status ' + esc(c.status) + '">' + esc(c.status) + '</span>' +
             '<div>' +
               '<span class="check-name">' + esc(c.name) + '</span>' +
               '<span class="check-msg">' + esc(c.message || '') + '</span>' +
             '</div></div>';
    }).join('');
    el.innerHTML =
      '<div class="audit-summary overall-' + esc(body.overall) + '">' +
        '<span class="badge ' + esc(body.overall) + '">' + esc(body.overall) + '</span>' +
        '<strong>' + esc(body.url) + '</strong>' +
        '<span>' + esc(body.summary || '') + '</span>' +
        '<span class="latency">' + esc(body.latency_ms) + ' ms</span>' +
      '</div>' +
      checksHTML +
      '<p style="text-align:center;margin-top:18px;font-size:0.85rem;color:var(--fg-50);">' +
        'Want this on every merchant in your catalog? ' +
        '<a href="/create-checkout-session?plan_id=pro" style="color:var(--accent);">Buy Pro</a> · ' +
        (body.remaining_today != null ? esc(body.remaining_today) + ' free audits left today' : '') +
      '</p>';
  }

  function runAudit(url, mode, results, submitBtn) {
    fetch('/audit-public', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: url, mode: mode})
    })
      .then(function (r) {
        return r.json().then(function (body) { return {status: r.status, body: body}; });
      })
      .then(function (resp) {
        renderResults(results, resp.body, resp.status);
      })
      .catch(function (e) {
        if (results) {
          results.innerHTML = '<div class="audit-error">Network error: ' + esc(e && e.message || e) + '</div>';
        }
      })
      .then(function () { if (submitBtn) submitBtn.disabled = false; });
  }

  function bind() {
    var form = $('auditForm');
    var results = $('auditResults');

    if (form) {
      // Defence in depth: even if the inline onsubmit guard ever returned the
      // wrong thing, this handler refuses to let the browser submit natively.
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var urlEl = $('auditUrl');
        var modeEl = $('auditMode');
        var submitBtn = form.querySelector('.audit-submit');
        if (!urlEl) return;
        var url = (urlEl.value || '').trim();
        var mode = modeEl ? modeEl.value : 'standard';
        if (!url) return;
        if (submitBtn) submitBtn.disabled = true;
        if (results) {
          results.hidden = false;
          results.innerHTML =
            '<div class="audit-loading"><span class="spinner"></span>Auditing ' + esc(url) + '…</div>';
        }
        runAudit(url, mode, results, submitBtn);
      });
    }

    // expose so the inline hint links can fill the input
    window.fillUrl = function (u) {
      var urlEl = $('auditUrl');
      if (urlEl) {
        urlEl.value = u;
        urlEl.focus();
      }
    };

    // Progressive enhancement: if we ever land at "/" with ?url=&mode= in the
    // query string (the symptom Ali reported — a degraded native GET submit),
    // refill the form fields from the query and dispatch a synthetic submit
    // so the user still sees a real audit result instead of a blank reset.
    // The synthetic submit fires the listener above, which calls
    // preventDefault() so no navigation happens.
    try {
      var sp = new URLSearchParams(window.location.search);
      var qUrl = sp.get('url');
      var qMode = sp.get('mode');
      if (form && qUrl) {
        var qUrlEl = $('auditUrl');
        var qModeEl = $('auditMode');
        if (qUrlEl) qUrlEl.value = qUrl;
        if (qModeEl && (qMode === 'standard' || qMode === 'marketplace')) {
          qModeEl.value = qMode;
        }
        form.dispatchEvent(new Event('submit', {cancelable: true, bubbles: true}));
        // Clean the URL so a manual refresh doesn't auto-run again.
        try {
          history.replaceState(
            null, '',
            window.location.pathname + (window.location.hash || '')
          );
        } catch (e2) { /* ignore — older browsers */ }
      }
    } catch (e) { /* ignore — graceful failure */ }

    // Flip the safety flag LAST, after bind() has succeeded. If anything above
    // threw, the flag stays false and the inline onsubmit guard will refuse
    // navigation on submit, surfacing an inline error to the user.
    window.__x402AuditReady = true;
  }

  // The script sits at the bottom of <body>, so DOM is normally already
  // parsed; DOMContentLoaded is a belt-and-suspenders guard against future
  // template reordering or async bundle splits.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
</script>

</body>
</html>
"""

# Shared page chrome moved to api_server.pages (imported at the top of this
# module as _PAGE_CSS / _PAGE_NAV / _PAGE_FOOTER).

_VS_DOCTOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>x402 Validator vs x402 Doctor — checker depth, compared honestly</title>
<meta name="description" content="x402 Doctor (Stelar Digital) is a quick free endpoint checker. x402 Validator is a strict-v2 conformance engine: 8 checks, marketplace walk, batch, MCP, PyPI, GitHub Action. Facts only.">
<link rel="canonical" href="https://x402-validator-tools.onrender.com/vs-x402-doctor">
<meta property="og:title" content="x402 Validator vs x402 Doctor">
<meta property="og:description" content="Quick checker vs strict-v2 conformance engine. What each one actually runs, verified against both products' own docs.">
<meta property="og:type" content="article">
<meta name="robots" content="index, follow">
<link rel="icon" type="image/png" href="/static/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>__PAGE_CSS__</style>
</head>
<body>
__PAGE_NAV__

<div class="wrap">
  <div class="kicker">Comparison · verified 2026-07-27</div>
  <h1 class="serif">x402 Validator vs x402 Doctor</h1>
  <p><a href="https://stelardigital.com/x402-doctor" rel="noopener">x402 Doctor</a> by Stelar Digital
     is a free, quick endpoint checker. x402 Validator (this service) is a strict-v2
     conformance engine. They overlap on the basics and diverge on depth.
     Everything below comes from both products' own public pages and repos.</p>

  <h2>What each one runs</h2>
  <table class="cmp">
    <tr><th>Capability</th><th>x402 Doctor</th><th>x402 Validator</th></tr>
    <tr><td>Reachability probe</td><td class="y">✓</td><td class="y">✓ (every check)</td></tr>
    <tr><td>Bot-wall / Cloudflare challenge detection</td><td class="y">✓</td><td class="y">✓ <code>bot_wall</code> <span style="color:var(--fg-50)">(added in 0.5.0 — credit where due: their writeup identified this failure mode well)</span></td></tr>
    <tr><td>Returns HTTP 402</td><td class="y">✓</td><td class="y">✓</td></tr>
    <tr><td>402 body parses as JSON object</td><td class="y">✓</td><td class="y">✓ <code>json_resilience</code> (CRITICAL_FAIL on primitives)</td></tr>
    <tr><td><code>x402Version</code> present / recognized</td><td class="y">✓</td><td class="y">✓ <code>accepts_completeness</code></td></tr>
    <tr><td><code>accepts[]</code> completeness (scheme, network, amount, payTo, resource)</td><td class="y">✓ presence</td><td class="y">✓ presence + <strong>atomic-units validation</strong> (flags <code>"0.005"</code> as dollars, off by 10⁶) + <code>resource.url</code> must match the probed URL</td></tr>
    <tr><td>CAIP-2 network validation</td><td class="y">✓</td><td class="y">✓ <code>caip2_compliance</code> — reads v2 <code>accepts[].network</code> (not just top level) and falls back to manifest-declared networks on free-discovery roots</td></tr>
    <tr><td>Bazaar input schema (<code>extensions.bazaar.info.input</code>)</td><td class="y">✓ presence</td><td class="y">✓ shape-validated against real production captures, per paid product</td></tr>
    <tr><td>Discovery doc (<code>/.well-known/x402</code>) exists</td><td class="y">✓</td><td class="y">✓ <code>manifest_discovery</code> + <code>discovery_resource_listing</code> (paid resource must actually be <em>listed</em> in it)</td></tr>
    <tr><td>Marketplace catalog walk (per-product 402 validation)</td><td class="n">—</td><td class="y">✓ marketplace mode: every product in the manifest gets its own endpoint audit + bazaar check</td></tr>
    <tr><td>Batch audits (many endpoints, one call)</td><td class="n">—</td><td class="y">✓ CLI + API</td></tr>
    <tr><td>Embeddable engine (PyPI package)</td><td class="n">—</td><td class="y">✓ <code>pip install x402-conformance-suite</code></td></tr>
    <tr><td>MCP server (agent-native tool)</td><td class="n">—</td><td class="y">✓ <code>x402-mcp</code></td></tr>
    <tr><td>GitHub Action (CI conformance gate)</td><td class="n">—</td><td class="y">✓</td></tr>
    <tr><td>Free tier</td><td class="y">10 checks/min</td><td class="y">3 audits/day per IP, then Pro from $9/mo</td></tr>
    <tr><td>Open source engine</td><td class="n">checker not published</td><td class="y">Apache-2.0, <a href="https://github.com/MSSATANASS/x402-conformance-engine" rel="noopener">full repo</a>, 203 tests</td></tr>
  </table>

  <h2>Where x402 Doctor wins</h2>
  <ul>
    <li><strong>Free tier generosity.</strong> 10 checks/minute beats our 3 audits/day for quick iteration. Their checker is the fastest way to sanity-check a fresh endpoint.</li>
    <li><strong>Content.</strong> Their pricing research and honest-numbers pages are some of the best reading in the x402 space. Their bot-wall writeup in particular names a failure mode most validators ignored — we added <code>bot_wall</code> in 0.5.0 partly because of it.</li>
    <li><strong>Setup service.</strong> They sell done-for-you x402 wiring ($149). We don't; we sell the audit engine, not consulting.</li>
  </ul>

  <h2>Where x402 Validator wins</h2>
  <ul>
    <li><strong>Depth per endpoint.</strong> Presence checks tell you a field exists; conformance checks tell you it's <em>right</em> — atomic units, CAIP-2 inside v2 <code>accepts[]</code>, resource/URL match, catalog listing.</li>
    <li><strong>Marketplaces.</strong> If your catalog page is intentionally free (HTTP 200) and only products are paid, a root-URL checker fails you. Our marketplace mode walks every product's own 402 and validates each one.</li>
    <li><strong>Embeddable everywhere.</strong> PyPI library, MCP server, GitHub Action, batch CLI — the engine runs in your CI and your agent framework, not just a web form.</li>
    <li><strong>Verifiable correctness.</strong> The engine is open source with 203 tests. When we shipped a CAIP-2 parser bug that false-failed v2 endpoints, a merchant reported it and we fixed, published, and emailed corrections the same day (<a href="/open">details on /open</a>).</li>
  </ul>

  <div class="note">
    <strong>One fact worth knowing about their starter kit.</strong> Stelar's
    <a href="https://github.com/StelarDigital/x402-starter-kit" rel="noopener">x402-starter-kit</a>
    ships <code>verify_payment()</code> as a deliberate stub — it accepts any non-empty
    <code>X-PAYMENT</code> header. Their README says so plainly (credit for honesty), but any
    endpoint deployed from that kit without replacing the stub is not actually charging anyone.
    Whichever tool you use: run it against a real endpoint before you trust the paywall.
  </div>

  <p style="margin-top:32px;">
    <a class="btn-primary-pill" href="/#audit" style="color:#fff;">Run a free audit — 3/day</a>
  </p>
</div>

__PAGE_FOOTER__
</body>
</html>
"""

_OPEN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Open — real numbers behind x402 Validator</title>
<meta name="description" content="Tests, corpus results, the CAIP-2 bug we shipped and fixed same-day, keys issued, revenue. Every number verifiable, none projected.">
<link rel="canonical" href="https://x402-validator-tools.onrender.com/open">
<meta property="og:title" content="Open — x402 Validator real numbers">
<meta property="og:description" content="Honest metrics: 203 tests, 27-endpoint corpus, one same-day bug fix, 5 free pro keys, $0 revenue so far.">
<meta property="og:type" content="website">
<meta name="robots" content="index, follow">
<link rel="icon" type="image/png" href="/static/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>__PAGE_CSS__</style>
</head>
<body>
__PAGE_NAV__

<div class="wrap">
  <div class="kicker">Open metrics · updated 2026-07-27</div>
  <h1 class="serif">Real numbers, no projections</h1>
  <p>Everything on this page is verifiable against the linked source. If a number
     isn't measurable yet, it says so instead of estimating.</p>

  <h2>Engine</h2>
  <div class="stat-grid">
    <div class="stat"><div class="num">0.5.0</div><div class="lbl">x402-conformance-suite on <a href="https://pypi.org/project/x402-conformance-suite/" rel="noopener">PyPI</a> (Apache-2.0)</div></div>
    <div class="stat"><div class="num">203</div><div class="lbl">tests in the engine suite, all passing at release</div></div>
    <div class="stat"><div class="num">7</div><div class="lbl">checks in a standard audit (4 before 0.5.0)</div></div>
    <div class="stat"><div class="num">27</div><div class="lbl">production endpoints in the v0.3 validation corpus, audited in 5.2 s</div></div>
  </div>

  <h2>The corpus result that matters</h2>
  <p>In the 2026-07-27 corpus run, <strong>0 of 27 endpoints passed</strong> all four v0.3
     checks. That is not a typo — most x402 endpoints in the wild are not conformant.
     Two of the 27 failed only CAIP-2 at the time; both pass today after the 0.4.1
     parser fix below. Full matrix:
     <a href="https://github.com/MSSATANASS/x402-conformance-engine/blob/feature/x402-core-engine/docs/VALIDATION_REPORT_v0.3.md" rel="noopener">VALIDATION_REPORT_v0.3.md</a>.</p>

  <h2>The bug we shipped (and how it went)</h2>
  <div class="note">
    Our pre-0.4.1 CAIP-2 parser read the network only at the top level of the payment
    payload. x402 v2 carries it at <code>accepts[].network</code> — so every strict-v2
    endpoint false-failed. Worse, we had already emailed merchants audit results
    containing that false FAIL.<br><br>
    A merchant replied with a precise diagnosis (free catalog root, paid products,
    network in <code>accepts[]</code>). Same day: reproduced live, fixed the parser,
    added manifest/product network fallbacks, published 0.4.1, redeployed, re-audited,
    and sent correction emails to the two merchants whose results changed.
    The full diff and tests are in the public repo history.
  </div>

  <h2>Business</h2>
  <div class="stat-grid">
    <div class="stat"><div class="num">5</div><div class="lbl">pro keys issued — all free 3-month early-adopter grants</div></div>
    <div class="stat"><div class="num">$0</div><div class="lbl">paying-customer revenue to date</div></div>
    <div class="stat"><div class="num">3/day</div><div class="lbl">free audits per IP; Pro is $9/mo, Enterprise $49/mo</div></div>
    <div class="stat"><div class="num">1</div><div class="lbl">person running this (Gael L Chulim), hosted on Render, billed via Stripe</div></div>
  </div>

  __WHAT_WE_TRACK__

  <h2>Sources</h2>
  <ul>
    <li>Engine repo + full test suite: <a href="https://github.com/MSSATANASS/x402-conformance-engine" rel="noopener">MSSATANASS/x402-conformance-engine</a></li>
    <li>This site's repo: <a href="https://github.com/MSSATANASS/x402-validator-tools" rel="noopener">MSSATANASS/x402-validator-tools</a></li>
    <li>PyPI release history: <a href="https://pypi.org/project/x402-conformance-suite/#history" rel="noopener">x402-conformance-suite</a></li>
    <li>Changelog with the 0.4.1 bug details: <a href="https://github.com/MSSATANASS/x402-conformance-engine/blob/feature/x402-core-engine/CHANGELOG.md" rel="noopener">CHANGELOG.md</a></li>
  </ul>
</div>

__PAGE_FOOTER__
</body>
</html>
"""


# "What we track" section for /open — picked per backend at render time.
# The JSON-backend copy is the historical text, unchanged.
_WHAT_WE_TRACK_JSON = """<h2>What we don't track</h2>
  <p>The API keeps no audit counter, no per-user history, and stores no audit results —
     only rate-limit timestamps per IP (in-memory, gone on restart) and issued API keys.
     When we can measure audits-served honestly, that number will appear here. Until
     then, it won't.</p>"""

_WHAT_WE_TRACK_DB = """<h2>What we track (and what we don't)</h2>
  <p>Since the database migration, every served audit writes one row: timestamp,
     URL, mode, overall result, latency, and the caller's plan tier. Public-demo
     rows carry no identity at all. We do not store the full check-by-check report,
     and we don't sell or share any of it. That is what makes the numbers below
     measured instead of projected.</p>
  <div class="stat-grid">
    <div class="stat"><div class="num">{total}</div><div class="lbl">audits served, all-time (live from the audit log)</div></div>
    <div class="stat"><div class="num">{this_month}</div><div class="lbl">audits served this month</div></div>
  </div>"""

_WHAT_WE_TRACK_DB_NO_STATS = """<h2>What we track (and what we don't)</h2>
  <p>Since the database migration, every served audit writes one row: timestamp,
     URL, mode, overall result, latency, and the caller's plan tier. Public-demo
     rows carry no identity at all. We do not store the full check-by-check report,
     and we don't sell or share any of it. The live audits-served counters are
     temporarily unavailable — we show real numbers or nothing.</p>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request) -> HTMLResponse:
    logged_in = False
    try:
        logged_in = auth_pages.current_user(request) is not None
    except Exception:
        logged_in = False  # the landing must never break on auth issues
    return HTMLResponse(
        _LANDING_HTML.replace("__AUTH_NAV__", _auth_nav_links(logged_in))
    )


@app.get("/vs-x402-doctor", response_class=HTMLResponse, include_in_schema=False)
async def vs_doctor() -> HTMLResponse:
    return HTMLResponse(
        _VS_DOCTOR_HTML.replace("__PAGE_CSS__", _PAGE_CSS)
        .replace("__PAGE_NAV__", _PAGE_NAV)
        .replace("__PAGE_FOOTER__", _PAGE_FOOTER)
    )


@app.get("/open", response_class=HTMLResponse, include_in_schema=False)
async def open_metrics() -> HTMLResponse:
    store = get_store()
    if getattr(store, "backend", "json") == "json":
        track_section = _WHAT_WE_TRACK_JSON
    else:
        try:
            stats = store.audit_stats()
            track_section = _WHAT_WE_TRACK_DB.format(
                total=stats["total"], this_month=stats["this_month"]
            )
        except Exception:
            track_section = _WHAT_WE_TRACK_DB_NO_STATS
    return HTMLResponse(
        _OPEN_HTML.replace("__PAGE_CSS__", _PAGE_CSS)
        .replace("__PAGE_NAV__", _PAGE_NAV)
        .replace("__PAGE_FOOTER__", _PAGE_FOOTER)
        .replace("__WHAT_WE_TRACK__", track_section)
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
    api_key: str = Depends(_require_api_key),
) -> ValidateResponse:
    store = get_store()
    plan_id = store.get(api_key)

    # Enforce the plan's monthly quota. The JSON keystore reports usage 0
    # (historical behavior); the PostgreSQL backend enforces for real.
    if not store.quota_allows(api_key, plan_id):
        plan = PLANS.get(plan_id or "")
        limit = plan.requests_per_month if plan else "your plan's"
        raise HTTPException(
            429,
            f"Monthly quota reached ({limit} audits/month on "
            f"{plan_id}). Upgrade: /create-checkout-session?plan_id=pro",
        )

    started = time.monotonic()
    try:
        report, probe, batch = await _run_audit(req.url, req.mode)
    except Exception as e:
        raise HTTPException(502, f"Audit failed: {e}")
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    checks = _flatten_checks(report)
    # Append tools-side checks, matching the _flatten_checks entry shape.
    checks.append(CheckResultItem(
        name=probe["check_name"],
        status=probe["status"],
        message=probe["message"],
        details=probe["details"],
    ))
    checks.append(CheckResultItem(
        name=batch["check_name"],
        status=batch["status"],
        message=batch["message"],
        details=batch["details"],
    ))
    overall, summary = _aggregate_check_results(checks)
    store.record_audit(
        url=report.target_url,
        mode=req.mode,
        overall=overall,
        latency_ms=elapsed_ms,
        caller_key=api_key,
        caller_plan=plan_id,
        source="api",
    )
    ai_advice = ai_summary = None
    ai_args = dict(
        url=report.target_url,
        overall=overall,
        summary=summary,
        checks=checks,
    )
    if req.advise and req.explain:
        ai_advice, ai_summary = await asyncio.gather(
            ai_advisor.advise(**ai_args),
            ai_advisor.summarize(**ai_args),
        )
    elif req.advise:
        ai_advice = await ai_advisor.advise(**ai_args)
    elif req.explain:
        ai_summary = await ai_advisor.summarize(**ai_args)
    return ValidateResponse(
        url=report.target_url,
        overall=overall,
        summary=summary,
        checks=checks,
        latency_ms=elapsed_ms,
        timestamp=report.timestamp.isoformat(),
        ai_advice=ai_advice,
        ai_summary=ai_summary,
    )


_DEFAULT_PUBLIC_DAILY_LIMIT = 3


@app.post("/audit-public")
async def audit_public(req: ValidateRequest, request: Request) -> dict:
    """Public, unauthenticated audit endpoint for the landing-page demo.

    Same engine as ``/validate`` but no API key required. Rate-limited to
    ``AUDIT_PUBLIC_DAILY_LIMIT`` calls per client IP per rolling 24h
    (default 5). Nudges the user toward Pro on the 429 path. Returns
    the standard audit JSON plus ``remaining_today``.
    """
    client_ip = (request.client.host if request.client and request.client.host
                 else "unknown")
    limit = int(
        os.environ.get("AUDIT_PUBLIC_DAILY_LIMIT", _DEFAULT_PUBLIC_DAILY_LIMIT)
    )
    limiter = ratelimit.get_limiter()
    if not limiter.allow(client_ip, limit):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily limit reached ({limit} per IP). Get unlimited audits "
                f"with Pro: /create-checkout-session?plan_id=pro"
            ),
        )
    started = time.monotonic()
    try:
        report, probe, batch = await _run_audit(req.url, req.mode)
    except Exception as e:
        raise HTTPException(502, f"Audit failed: {e}")
    elapsed_ms = round((time.monotonic() - started) * 1000, 2)
    checks = [
        {
            "name": c.check_name,
            "status": c.status,
            "message": c.message,
            "details": c.details,
        }
        for c in report.checks
    ]
    # Append tools-side checks, matching the entry shape above.
    checks.append({
        "name": probe["check_name"],
        "status": probe["status"],
        "message": probe["message"],
        "details": probe["details"],
    })
    checks.append({
        "name": batch["check_name"],
        "status": batch["status"],
        "message": batch["message"],
        "details": batch["details"],
    })
    overall, summary = _aggregate_check_results(checks)
    get_store().record_audit(
        url=report.target_url,
        mode=req.mode,
        overall=overall,
        latency_ms=elapsed_ms,
        caller_key=None,
        caller_plan=None,
        source="public",
    )
    return {
        "url": report.target_url,
        "overall": overall,
        "summary": summary,
        "checks": checks,
        "latency_ms": elapsed_ms,
        "timestamp": report.timestamp.isoformat(),
        "remaining_today": limiter.remaining(client_ip, limit),
    }


@app.post("/create-checkout-session", response_model=CheckoutResponse)
async def create_checkout_session(plan_id: str) -> CheckoutResponse:
    if plan_id not in PLANS:
        raise HTTPException(400, f"Unknown plan: {plan_id!r}")

    base = os.environ.get("PUBLIC_URL", "https://x402-validator-tools.onrender.com")
    try:
        url = stripe_integration.create_checkout_session(
            plan_id,
            success_url=f"{base}/success?session_id={{CHECKOUT_SESSION_ID}}",
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


@app.get("/create-checkout-session", response_class=RedirectResponse, include_in_schema=False)
async def create_checkout_session_link(plan_id: str) -> RedirectResponse:
    """Redirect-friendly GET: lets ``<a href="...plan_id=pro">`` buttons go straight
    to Stripe. Mirrors the POST handler but returns 302 instead of JSON."""
    if plan_id not in PLANS:
        raise HTTPException(400, f"Unknown plan: {plan_id!r}")

    base = os.environ.get("PUBLIC_URL", "https://x402-validator-tools.onrender.com")
    url = stripe_integration.create_checkout_session(
        plan_id,
        success_url=f"{base}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/cancel",
    )
    if url is not None:
        return RedirectResponse(url, status_code=303)
    if PLANS[plan_id].price_cents == 0:
        return RedirectResponse(f"{base}/success", status_code=303)
    raise HTTPException(503, "Stripe is not configured (set STRIPE_SECRET_KEY)")


class StripeWebhookPayload(BaseModel):
    type: str
    data: Optional[dict] = None


@app.post("/stripe-webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(..., alias="stripe-signature"),
) -> dict:
    """Verify a Stripe webhook and dispatch the event.

    On ``checkout.session.completed`` we mint an API key for the matching
    plan, persist the ``session_id → key`` claim, and let ``/success`` surface
    the key to the buyer. Idempotent: re-delivery of the same checkout event
    finds the claim already populated (different api_key would be minted —
    we gate on existing-session check to avoid that).
    """
    body = await request.body()
    event = stripe_integration.verify_webhook(body, stripe_signature)
    if event is None:
        raise HTTPException(400, "Invalid signature or Stripe not configured")

    event_type = event.get("type") or event.get("event_type")

    if event_type == "checkout.session.completed":
        session_obj = (event.get("data") or {}).get("object") or {}
        session_id = session_obj.get("id")
        if not session_id:
            return {"received": True, "type": event_type, "minted": False,
                    "reason": "missing session id"}

        # Idempotency: if a claim already exists for this session, don't
        # re-mint (would lose the buyer's original key).
        existing = get_store().claim_by_session(session_id)
        if existing:
            return {"received": True, "type": event_type, "minted": False,
                    "reason": "claim already exists", "session_id": session_id}

        detail = stripe_integration.retrieve_session(session_id) or {}
        if not detail:
            return {"received": True, "type": event_type, "minted": False,
                    "reason": "could not retrieve session"}

        # Plan id preference order: session metadata, then amount_total fallback.
        plan_id = (detail.get("metadata") or {}).get("plan_id")
        if not plan_id or plan_id not in PLANS:
            amount = detail.get("amount_total") or 0
            for candidate in ("enterprise", "pro"):
                if amount == PLANS[candidate].price_cents:
                    plan_id = candidate
                    break
        if not plan_id or plan_id not in PLANS:
            return {"received": True, "type": event_type, "minted": False,
                    "reason": "could not resolve plan", "session_id": session_id}

        customer_id = detail.get("customer")

        # Checkouts started from a logged-in account carry
        # client_reference_id="user:<id>": link the purchase to it so the
        # key appears in the user's dashboard. Never lose a payment on a
        # linking failure — fall back to the anonymous flow.
        linked_user_id = None
        ref = detail.get("client_reference_id") or ""
        if ref.startswith("user:"):
            try:
                candidate = int(ref.split(":", 1)[1])
            except ValueError:
                candidate = None
            if candidate is not None:
                user_store = auth.get_user_store()
                if user_store is not None and user_store.get_user(candidate):
                    try:
                        user_store.link_purchase(
                            candidate, plan_id, customer_id, session_id
                        )
                        linked_user_id = candidate
                    except Exception:
                        linked_user_id = None

        if linked_user_id is None:
            get_store().issue(
                plan_id, customer_id=customer_id, session_id=session_id
            )

        result = {"received": True, "type": event_type, "minted": True,
                  "plan_id": plan_id, "session_id": session_id}
        if linked_user_id is not None:
            result["user_id"] = linked_user_id
        return result

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
# Account routes (signup / login / dashboard — api_server.auth_pages)
# ---------------------------------------------------------------------------

app.include_router(auth_pages.router)


# ---------------------------------------------------------------------------
# Success / cancel pages (after Stripe checkout)
# ---------------------------------------------------------------------------


_SUCCESS_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Payment received · x402 validator</title>
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#000;--fg:#fff;--fg-70:rgba(255,255,255,0.70);--accent:#3054ff;}
*{box-sizing:border-box;}
html,body{margin:0;background:var(--bg);color:var(--fg);}
body{font-family:'Instrument Sans',-apple-system,sans-serif;padding:64px 24px;min-height:100vh;display:flex;align-items:center;justify-content:center;}
.card{max-width:520px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);border-radius:18px;padding:40px 32px;text-align:center;backdrop-filter:blur(12px);}
h1{font-size:clamp(28px,4vw,42px);margin:0 0 12px;line-height:1.05;letter-spacing:-0.025em;}
p{color:var(--fg-70);margin:0 0 16px;line-height:1.6;}
a{color:var(--accent);text-decoration:none;}
</style>
</head>
<body>
<div class="card">
  <h1>Payment received</h1>
  <p>Your key is still being issued — our webhook usually finishes within a
     few seconds. Refresh this page in a moment, or contact
     <a href="https://github.com/MSSATANASS/x402-validator-tools/issues">GitHub Issues</a>
     with your Stripe receipt if it doesn't show up.</p>
  <p>You can also return to the <a href="/">home page</a>.</p>
</div>
</body>
</html>"""


_SUCCESS_WITH_KEY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Payment received · x402 validator</title>
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#000;--fg:#fff;--fg-70:rgba(255,255,255,0.70);--accent:#3054ff;--accent-hover:#2040e0;--warn:#fbbf24;}
*{box-sizing:border-box;}
html,body{margin:0;background:var(--bg);color:var(--fg);}
body{font-family:'Instrument Sans',-apple-system,sans-serif;padding:64px 24px;min-height:100vh;display:flex;align-items:center;justify-content:center;}
.card{max-width:560px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);border-radius:18px;padding:40px 32px;text-align:center;backdrop-filter:blur(12px);}
.tag{display:inline-block;padding:6px 14px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);border-radius:999px;font-size:12px;color:var(--fg-70);letter-spacing:0.06em;text-transform:uppercase;margin-bottom:12px;}
h1{font-size:clamp(28px,4vw,42px);margin:8px 0 8px;line-height:1.05;letter-spacing:-0.025em;}
p.lede{color:var(--fg-70);margin:0 0 24px;line-height:1.55;}
.key-box{background:rgba(0,0,0,0.6);border:1px solid var(--accent);border-radius:12px;padding:14px 16px;margin:24px 0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;word-break:break-all;user-select:all;text-align:left;line-height:1.4;}
.copy-btn{background:var(--accent);color:#fff;border:none;padding:12px 24px;border-radius:999px;font-weight:600;cursor:pointer;font-size:14px;font-family:inherit;display:inline-flex;align-items:center;gap:8px;}
.copy-btn:hover{background:var(--accent-hover);}
.warn{color:var(--warn);font-size:13px;margin-top:24px;line-height:1.5;text-align:left;}
a{color:var(--accent);text-decoration:none;}
</style>
</head>
<body>
<div class="card">
  <span class="tag">__PLAN_LABEL__ plan</span>
  <h1>Payment received</h1>
  <p class="lede">Save this API key — we will not show it again.</p>
  <div class="key-box" id="keyBox">__API_KEY__</div>
  <button class="copy-btn" id="copyBtn" type="button">Copy key</button>
  __OWNER_NOTE__
  <p class="warn">⚠ Treat it like a password. Refreshing this page removes it from our
     view; if you lose it, mint a replacement from your dashboard
     or contact <a href="https://github.com/MSSATANASS/x402-validator-tools/issues">GitHub Issues</a>.</p>
</div>
<script>
(function(){
  var btn = document.getElementById('copyBtn');
  if(!btn || !navigator.clipboard) return;
  btn.addEventListener('click', function(){
    navigator.clipboard.writeText(document.getElementById('keyBox').innerText).then(function(){
      btn.innerText = 'Copied ✓';
      setTimeout(function(){ btn.innerText = 'Copy key'; }, 2000);
    }).catch(function(){ btn.innerText = 'Select + ⌘C'; });
  });
})();
</script>
</body>
</html>"""


_PLAN_LABELS = {"free": "Free", "pro": "Pro", "enterprise": "Enterprise"}


def _success_html(api_key: str, plan_id: str, session_id: str,
                  owner_note: str = "") -> HTMLResponse:
    import html as _html
    return HTMLResponse(
        _SUCCESS_WITH_KEY_HTML
        .replace("__API_KEY__", _html.escape(api_key))
        .replace("__PLAN_LABEL__", _html.escape(_PLAN_LABELS.get(plan_id, plan_id.title())))
        .replace("__SESSION_ID__", _html.escape(session_id))
        .replace("__OWNER_NOTE__", owner_note)
    )


@app.get("/success", response_class=HTMLResponse, include_in_schema=False)
async def success_page(request: Request,
                       session_id: Optional[str] = None) -> HTMLResponse:
    """Display a one-time key view when ``session_id`` is valid, fall back otherwise."""
    if session_id:
        claim = get_store().claim_by_session(session_id)
        if claim and get_store().get(claim["api_key"]) is not None:
            note = ""
            try:
                user = auth_pages.current_user(request)
                user_store = auth.get_user_store()
                if user and user_store and \
                        user_store.key_owner(claim["api_key"]) == user["id"]:
                    note = ('<p style="color:var(--fg-70);font-size:13px;'
                            'margin-top:16px;">This key is also listed in '
                            '<a href="/dashboard">your dashboard</a>.</p>')
            except Exception:
                note = ""  # never break the key view
            html = _success_html(claim["api_key"], claim["plan_id"],
                                 session_id, note)
            get_store().mark_claimed(session_id)
            return html
    return HTMLResponse(_SUCCESS_FALLBACK_HTML)


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
