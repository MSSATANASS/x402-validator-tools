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
from fastapi.responses import HTMLResponse, RedirectResponse
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
<title>x402 Validator — strict-v2 conformance audits · Manifest, CAIP-2, JSON, Bazaar</title>
<meta name="description" content="Audit any x402 merchant endpoint for strict-v2 conformance in ~580 ms. Live demo, no signup · Free, Pro ($9/mo), Enterprise ($49/mo). Hosted by Gael L Chulim.">
<link rel="canonical" href="https://lastminutestickets.com/">
<meta property="og:title" content="x402 Validator — strict-v2 conformance audits">
<meta property="og:description" content="Manifest, CAIP-2, JSON resilience, Bazaar compliance. Operator-actionable results in ~580 ms. Free demo + Pro API.">
<meta property="og:type" content="website">
<meta property="og:url" content="https://lastminutestickets.com/">
<meta property="og:site_name" content="x402 validator">
<meta property="og:image" content="https://lastminutestickets.com/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="x402 Validator — strict-v2 conformance audits">
<meta name="twitter:description" content="Audit any x402 merchant in ~580 ms. Free demo + Pro API. Hosted on Render · Billed via Stripe.">
<meta name="robots" content="index, follow">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='6' fill='%233054ff'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='white' font-family='sans-serif' font-weight='700' font-size='14'>x4</text></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preconnect" href="https://stream.mux.com" crossorigin>
<link rel="preconnect" href="https://images.unsplash.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/hls.js@1.6.15/dist/hls.min.js"></script>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "SoftwareApplication",
      "name": "x402 Validator",
      "url": "https://lastminutestickets.com/",
      "applicationCategory": "DeveloperApplication",
      "applicationSubCategory": "API Service / Testing Tool",
      "operatingSystem": "Any (hosted REST API)",
      "description": "Conformance audit API for x402 strict-v2 merchant endpoints. Runs Manifest, CAIP-2, JSON resilience, and Bazaar compliance checks against any URL and returns operator-actionable JSON.",
      "offers": [
        {"@type": "Offer", "name": "Free", "price": "0", "priceCurrency": "USD", "description": "100 audits / month · forever · no signup"},
        {"@type": "Offer", "name": "Pro", "price": "9", "priceCurrency": "USD", "description": "500 audits / month · API key · marketplace mode · email support"},
        {"@type": "Offer", "name": "Enterprise", "price": "49", "priceCurrency": "USD", "description": "5,000 audits / month · bulk · priority support · volume rebate"}
      ],
      "creator": {"@type": "Person", "name": "Gael L Chulim", "email": "gael@lastminutestickets.com"},
      "license": "https://www.apache.org/licenses/LICENSE-2.0"
    },
    {
      "@type": "FAQPage",
      "mainEntity": [
        {"@type": "Question", "name": "What is x402 conformance and why should I care?",
         "acceptedAnswer": {"@type": "Answer", "text": "x402 is the HTTP-402-based payment protocol from Coinbase. Strict-v2 conformance means your merchant endpoint serves a Bazaar-compliant manifest, advertises its CAIP-2 network/asset identifiers, returns resilient JSON, and exposes the 402 channel your buyers need. If any of those checks fail, gateways refuse to list you and customers see cryptic errors. This API runs all four checks in ~580 ms and returns actionable operator errors."}},
        {"@type": "Question", "name": "What does the public demo actually check?",
         "acceptedAnswer": {"@type": "Answer", "text": "The same four checks as /validate: manifest_discovery, caip2_compliance, json_resilience, bazaar_compliance. Rate-limited to 5 audits per IP per day."}},
        {"@type": "Question", "name": "How long does an audit take?",
         "acceptedAnswer": {"@type": "Answer", "text": "Median ~580 ms end-to-end. Hits the endpoint, parses the response, runs all four checks in parallel where independent."}},
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
  --bg: #000000;
  --fg: #ffffff;
  --fg-80: rgba(255,255,255,0.80);
  --fg-70: rgba(255,255,255,0.70);
  --fg-60: rgba(255,255,255,0.60);
  --fg-50: rgba(255,255,255,0.50);
  --accent: #3054ff;
  --accent-hover: #2040e0;
  --gradient-end: #b4c0ff;
  --primary-text-dark: #0a0400;
  --glass-border: rgba(255,255,255,0.10);
  --hero-grad: linear-gradient(to left, #6366f1, #a855f7, #fcd34d);
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #000; }

body {
  font-family: 'Instrument Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;
  color: var(--fg);
  background: #000;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  line-height: 1.5;
  overflow-x: hidden;
}

button { font-family: inherit; cursor: pointer; border: none; background: none; color: inherit; }
a { color: inherit; }

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
.nav-left .icon { width: 24px; height: 24px; display: block; }

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
.btn-primary-pill:hover { box-shadow: 0 0 20px rgba(255,255,255,0.3); }

@media (min-width: 640px) { .book-demo { display: inline-flex; } }
@media (min-width: 768px) { .nav-links { display: flex; } }

/* ============================================================
   HERO SECTION (full screen) — motion-style
   ============================================================ */
.hero {
  position: relative;
  width: 100%;
  min-height: 100vh;
  background: #000000;
  color: var(--fg);
  overflow: hidden;
  display: flex; flex-direction: column;
}

.hero-video-wrap {
  position: absolute; inset: 0;
  z-index: 0;
  overflow: hidden;
}
.hero-video {
  position: absolute; inset: 0;
  width: 100%; height: 100%;
  object-fit: cover; opacity: 0.6;
}

.hero-overlay {
  position: absolute; inset: 0;
  background: rgba(0,0,0,0.6);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
  z-index: 1;
}

.hero-decor {
  position: absolute;
  border-radius: 50%;
  filter: blur(120px);
  mix-blend-mode: screen;
  pointer-events: none;
  z-index: 2;
}
.hero-decor.tl {
  top: -20%; left: 20%;
  width: 600px; height: 600px;
  background: rgba(30,58,138,0.20);  /* blue-900/20 */
}
.hero-decor.br {
  bottom: -10%; right: 20%;
  width: 500px; height: 500px;
  background: rgba(49,46,129,0.20);  /* indigo-900/20 */
}

.hero-content {
  position: relative;
  z-index: 10;
  max-width: 64rem; /* max-w-5xl */
  margin: 0 auto;
  width: 100%;
  padding: 144px 24px 64px;
  text-align: center;
  display: flex; flex-direction: column; align-items: center;
  gap: 48px;
}

/* ============================================================
   HeroCopy — pre-headline (serif), main (gradient), sub
   ============================================================ */
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
  background: linear-gradient(to bottom, #ffffff, #ffffff, #b4c0ff);
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
  max-width: 36rem;  /* max-w-xl */
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
  align-items: center; gap: 24px;
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
  box-shadow: 0 0 20px rgba(255,255,255,0.30);
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
  background: var(--accent);
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--fg);
  transition: background 0.2s;
}
.cta-primary:hover .arrow { background: var(--accent-hover); }
.cta-primary .arrow svg { width: 20px; height: 20px; }

.cta-secondary {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 8px 16px;
  background: rgba(255,255,255,0.06);
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
  background: rgba(255,255,255,0.05);
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
  background: rgba(255,255,255,0.04);
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
  background: rgba(255,255,255,0.04);
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
  box-shadow: 0 16px 48px -16px rgba(48,84,255,0.35);
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
  border-top: 1px solid rgba(255,255,255,0.08);
}
.plan-features li:last-child { border-bottom: 1px solid rgba(255,255,255,0.08); }
.plan-features li svg { flex-shrink: 0; color: #10b981; width: 16px; height: 16px; }

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
.plan-cta.outline { background: transparent; color: var(--fg); border: 1px solid rgba(255,255,255,0.15); }

/* ============================================================
   Footer
   ============================================================ */
footer {
  border-top: 1px solid rgba(255,255,255,0.08);
  padding: 64px 24px 80px;
  color: var(--fg-70);
}
.foot-grid {
  display: grid; gap: 32px;
  grid-template-columns: 1.4fr repeat(3, 1fr);
  max-width: 1100px; margin: 0 auto;
}
@media (max-width: 760px) { .foot-grid { grid-template-columns: 1fr 1fr; } }
.foot-brand .mark {
  width: 32px; height: 32px;
  border-radius: 8px;
  background: var(--hero-grad);
  display: inline-flex; align-items: center; justify-content: center;
  color: #000; font-weight: 800;
  font-family: 'Instrument Sans', sans-serif;
  font-size: 0.85rem;
}
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
  border-top: 1px solid rgba(255,255,255,0.08);
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
  border-top: 1px solid rgba(255,255,255,0.06);
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
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.10);
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
  background: rgba(0,0,0,0.4);
  border: 1px solid rgba(255,255,255,0.12);
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
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(48,84,255,0.25);
}
.audit-form select {
  background: rgba(0,0,0,0.4);
  border: 1px solid rgba(255,255,255,0.12);
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
.audit-submit:hover { background: var(--accent-hover); }
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
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.10);
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
.audit-summary .badge.PASS { background: rgba(16,185,129,0.18); color: #34d399; }
.audit-summary .badge.FAIL { background: rgba(239,68,68,0.18); color: #fca5a5; }
.audit-summary .latency { color: var(--fg-50); font-size: 0.85rem; margin-left: auto; }

.check-row {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.06);
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
.check-row .check-status.PASS { color: #34d399; }
.check-row .check-status.FAIL { color: #fca5a5; }
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
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 10px;
  padding: 14px 16px;
  font-family: 'Instrument Sans', sans-serif;
  font-size: 0.92rem;
  color: var(--fg-70);
  display: flex; align-items: center; gap: 10px;
}
.audit-loading .spinner {
  width: 14px; height: 14px;
  border: 2px solid rgba(255,255,255,0.18);
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
  border-top: 1px solid rgba(255,255,255,0.06);
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
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 12px;
  padding: 0;
  transition: border-color 0.15s;
}
.faq-item:hover { border-color: rgba(255,255,255,0.14); }
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
.faq-item[open] summary::after { content: '−'; color: var(--accent); }
.faq-item p {
  padding: 0 20px 18px;
  margin: 0;
  color: var(--fg-70);
  font-size: 0.95rem;
  line-height: 1.65;
}
.faq-item p code {
  background: rgba(255,255,255,0.06);
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
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="1.8" stroke-linecap="round">
      <circle cx="12" cy="12" r="3.5"/>
      <line x1="12" y1="2" x2="12" y2="6"/>
      <line x1="12" y1="18" x2="12" y2="22"/>
      <line x1="2" y1="12" x2="6" y2="12"/>
      <line x1="18" y1="12" x2="22" y2="12"/>
      <line x1="4.93" y1="4.93" x2="7.64" y2="7.64"/>
      <line x1="16.36" y1="16.36" x2="19.07" y2="19.07"/>
      <line x1="4.93" y1="19.07" x2="7.64" y2="16.36"/>
      <line x1="16.36" y1="7.64" x2="19.07" y2="4.93"/>
    </svg>
  </div>

  <div class="nav-links">
    <a href="#pricing">Products
      <svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
    </a>
    <a href="#stories">Customer Stories</a>
    <a href="/docs">Resources</a>
    <a href="#pricing">Pricing</a>
  </div>

  <div class="nav-right">
    <a class="book-demo" href="#pricing">Book A Demo</a>
    <a class="btn-primary-pill" href="/create-checkout-session?plan_id=pro">Get Started</a>
  </div>
</nav>

<!-- =================== HERO SECTION (motion-style) =================== -->
<section class="hero">
  <!-- HLS video bg with poster fallback -->
  <div class="hero-video-wrap">
    <video id="heroVideo" class="hero-video" muted loop playsinline preload="auto"
           poster="https://images.unsplash.com/photo-1647356191320-d7a1f80ca777?crop=entropy&cs=tinysrgb&fit=max&fm=jpg&ixid=M3w3Nzg4Nzd8MHwxfHNlYXJjaHwxfHxhYnN0cmFjdCUyMGRhcmslMjB0ZWNobm9sb2d5JTIwbmV1cmFsJTIwbmV0d29ya3xlbnwxfHx8fDE3Njg5NzIyNTV8MA&ixlib=rb-4.1.0&q=80&w=1080"></video>
  </div>
  <div class="hero-overlay"></div>
  <div class="hero-decor tl"></div>
  <div class="hero-decor br"></div>

  <div class="hero-content">
    <p class="pre-headline anim-fade-up">Design at the speed of thought</p>
    <h1 class="main-headline anim-scale">Build Faster</h1>
    <p class="sub-headline">Create fully functional, SEO-optimized websites in seconds with our advanced AI engine.</p>
    <div class="hero-ctas anim-fade-up-late">
      <a class="cta-primary" href="/create-checkout-session?plan_id=pro">
        <span class="label">Start Building Free</span>
        <span class="arrow">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
        </span>
      </a>
      <a class="cta-secondary" href="#pricing">
        See Examples
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
      </a>
    </div>
  </div>
</section>

<!-- =================== AUDIT DEMO (interactive, free, rate-limited) =================== -->
<section id="audit" class="audit-demo-section">
  <div class="pricing-eyebrow-row"><span class="pricing-pill">Live demo · No signup</span></div>
  <h2 class="audit-demo-headline">Audit an x402 endpoint right now</h2>
  <p class="audit-demo-sub">Paste any merchant URL. We run Manifest, CAIP-2, JSON resilience, and Bazaar compliance checks against it. No API key, no signup. <strong>5 audits per IP per day</strong> on the public demo.</p>

  <form id="auditForm" class="audit-form" autocomplete="off">
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
    <p class="audit-hint" id="auditHint">Real audited endpoints: <a href="#" onclick="fillUrl('https://observer.137-184-67-179.sslip.io');return false">observer</a> · <a href="#" onclick="fillUrl('https://defi.hugen.tokyo');return false">hugen</a> · <a href="#" onclick="fillUrl('https://stabletravel.dev');return false">travel</a></p>
  </form>

  <div id="auditResults" class="audit-results" aria-live="polite" hidden></div>
</section>

<!-- =================== PRICING (kept, restyled to dark theme) =================== -->
<section id="pricing" class="pricing-section">
  <div class="pricing-eyebrow-row"><span class="pricing-pill">Pricing</span></div>
  <h2 class="pricing-headline">Audit at the speed of thought. <br/>Pick a plan below.</h2>
  <p class="pricing-sub">Stripe-billed. No long-term contract. Cancel from your dashboard anytime.</p>

  <div class="pricing-grid">
    <div class="plan">
      <h3 class="plan-name">Free</h3>
      <p class="plan-desc">For trying it out on a single merchant.</p>
      <div class="plan-value"><div class="plan-price">$0</div><span class="plan-period-small">/mo</span></div>
      <div class="plan-period-line">100 audits / month · forever</div>
      <ul class="plan-features">
        <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>All 4 conformance checks</li>
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
      <p>x402 is the HTTP-402-based payment protocol from Coinbase. Strict-v2 conformance means your merchant endpoint serves a Bazaar-compliant manifest, advertises its CAIP-2 network/asset identifiers, returns resilient JSON, and exposes the 402 channel your buyers need. If any of those checks fail, gateways refuse to list you and customers see cryptic errors. This API runs all four checks in ~580 ms and returns actionable operator errors.</p>
    </details>
    <details class="faq-item">
      <summary>What does the public demo actually check?</summary>
      <p>The same four checks as <code>/validate</code>: <code>manifest_discovery</code>, <code>caip2_compliance</code>, <code>json_resilience</code>, <code>bazaar_compliance</code>. The demo is rate-limited to 5 audits per IP per day — that's enough to convince you, not enough to abuse. Buy a Pro key for 500 audits/month; Enterprise gets you 5,000.</p>
    </details>
    <details class="faq-item">
      <summary>Is the public demo really free? What about my data?</summary>
      <p>Yes, the demo is free and requires no signup. We log the URL you submit and your IP only for abuse detection (matching the rate limit). We do not sell, share, or persist the audit results anywhere. Buy a key and the same engine runs against your merchant endpoints; results are returned to you only.</p>
    </details>
    <details class="faq-item">
      <summary>How long does an audit take?</summary>
      <p>Median <strong>~580 ms</strong> end-to-end. We hit your endpoint, parse the response, run all four checks in parallel where independent, and return structured JSON. Failing checks ship with operator-actionable messages, not stack traces.</p>
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
      <p>x402 validator is built and operated by Gael L Chulim (<a href="mailto:gael@lastminutestickets.com">gael@lastminutestickets.com</a>). The engine is Apache-2.0 and open source (<a href="https://github.com/MSSATANASS/x402-conformance-engine" rel="noopener">GitHub</a>); the audit API is a hosted service on Render and billed through Stripe.</p>
    </details>
  </div>
</section>

<!-- =================== FOOTER =================== -->
<footer>
  <div class="foot-grid">
    <div class="foot-brand">
      <div class="mark">x4</div>
      <div class="name">x402 validator</div>
      <div class="tag">REST API that runs the x402 strict-v2 conformance suite against any URL.</div>
    </div>
    <div class="foot-col">
      <h4>Product</h4>
      <a href="#pricing">Pricing</a>
      <a href="/plans">Plans API</a>
      <a href="/health">Status</a>
      <a href="mailto:support@lastminutestickets.com">Support</a>
    </div>
    <div class="foot-col">
      <h4>Code</h4>
      <a href="https://github.com/MSSATANASS/x402-validator-tools" rel="noopener">Tools (this site)</a>
      <a href="https://github.com/MSSATANASS/x402-conformance-engine" rel="noopener">Engine fork</a>
      <a href="https://github.com/smartflowproai-lang/x402-endpoint-validator" rel="noopener">Upstream</a>
      <a href="https://pypi.org/project/x402-validator/" rel="noopener">pip install</a>
    </div>
    <div class="foot-col">
      <h4>Contact</h4>
      <a href="mailto:support@lastminutestickets.com">support@lastminutestickets.com</a>
      <a href="https://github.com/MSSATANASS/x402-validator-tools/issues" rel="noopener">GitHub issues</a>
      <a href="mailto:gael@lastminutestickets.com">gael@lastminutestickets.com</a>
    </div>
  </div>
  <div class="foot-bottom">
    <div>© 2026 x402 validator · Apache-2.0</div>
    <div>stripe • persistent key store in <code>api_keys.json</code></div>
  </div>
</footer>

<script>
// === HLS.js video loader with Safari fallback ===
// Spec contract: muted, loop, playsInline, object-fit cover, opacity 0.6,
// poster fallback if HLS fails / unsupported.
(function () {
  const video = document.getElementById('heroVideo');
  if (!video) return;
  const src = 'https://stream.mux.com/T6oQJQ02cQ6N01TR6iHwZkKFkbepS34dkkIc9iukgy400g.m3u8';

  function play() {
    video.play().catch(function (e) { console.log('Auto-play prevented:', e); });
  }

  if (window.Hls && Hls.isSupported()) {
    const hls = new Hls();
    hls.loadSource(src);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, play);
    window.addEventListener('beforeunload', function () { hls.destroy(); });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    video.src = src;
    video.addEventListener('loadedmetadata', play);
  }
  // else: poster image stays as the visual fallback.
})();

// === Audit-demo form handler ===
(function () {
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
        '<div class="audit-rate">Daily limit reached (5 per IP). ' +
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

  var form = $('auditForm');
  var results = $('auditResults');
  if (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var urlEl = $('auditUrl');
      var modeEl = $('auditMode');
      var submitBtn = form.querySelector('.audit-submit');
      var url = urlEl.value.trim();
      var mode = modeEl.value;
      if (!url) return;
      submitBtn.disabled = true;
      results.hidden = false;
      results.innerHTML =
        '<div class="audit-loading"><span class="spinner"></span>Auditing ' + esc(url) + '…</div>';
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
          results.innerHTML = '<div class="audit-error">Network error: ' + esc(e.message) + '</div>';
        })
        .then(function () { submitBtn.disabled = false; });
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
})();
</script>

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


_DEFAULT_PUBLIC_DAILY_LIMIT = 5


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
        report = await _run_audit(req.url, req.mode)
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
    return {
        "url": report.target_url,
        "overall": report.overall_status,
        "summary": report.summary,
        "checks": checks,
        "latency_ms": elapsed_ms,
        "timestamp": report.timestamp.isoformat(),
        "remaining_today": limiter.remaining(client_ip, limit),
    }


@app.post("/create-checkout-session", response_model=CheckoutResponse)
async def create_checkout_session(plan_id: str) -> CheckoutResponse:
    if plan_id not in PLANS:
        raise HTTPException(400, f"Unknown plan: {plan_id!r}")

    base = os.environ.get("PUBLIC_URL", "https://lastminutestickets.com")
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

    base = os.environ.get("PUBLIC_URL", "https://lastminutestickets.com")
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
        token = get_store().issue(
            plan_id, customer_id=customer_id, session_id=session_id
        )
        return {"received": True, "type": event_type, "minted": True,
                "plan_id": plan_id, "session_id": session_id}

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
  <p>Your key has been emailed to you. If you don't see it within 5 minutes,
     check spam or contact <a href="mailto:support@lastminutestickets.com">support@lastminutestickets.com</a>.</p>
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
  <p class="warn">⚠ Treat it like a password. Refreshing this page removes it from our
     view; if you lose it, mint a replacement from your dashboard
     or contact <a href="mailto:support@lastminutestickets.com">support@lastminutestickets.com</a>.</p>
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


def _success_html(api_key: str, plan_id: str, session_id: str) -> HTMLResponse:
    import html as _html
    return HTMLResponse(
        _SUCCESS_WITH_KEY_HTML
        .replace("__API_KEY__", _html.escape(api_key))
        .replace("__PLAN_LABEL__", _html.escape(_PLAN_LABELS.get(plan_id, plan_id.title())))
        .replace("__SESSION_ID__", _html.escape(session_id))
    )


@app.get("/success", response_class=HTMLResponse, include_in_schema=False)
async def success_page(session_id: Optional[str] = None) -> HTMLResponse:
    """Display a one-time key view when ``session_id`` is valid, fall back otherwise."""
    if session_id:
        claim = get_store().claim_by_session(session_id)
        if claim and get_store().get(claim["api_key"]) is not None:
            html = _success_html(claim["api_key"], claim["plan_id"], session_id)
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
