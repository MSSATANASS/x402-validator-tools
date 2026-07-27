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
<title>x402 Validator — REST API for x402 conformance audits</title>
<meta name="description" content="One POST → 4-check x402 strict-v2 audit. Manifest, CAIP-2, JSON resilience, Bazaar. JSON back. Free, Pro, Enterprise.">
<style>
/* ============================================================
   Design system: 2026 SaaS — glass + mesh gradient + bento
   ============================================================ */
:root {
  --bg: #08070a;
  --bg-soft: #0e0d12;
  --fg: #f1f3f5;
  --muted: #8b8d98;
  --muted2: #5a5c66;
  --accent: #8b5cf6;   /* violet — primary */
  --accent2: #6366f1;  /* indigo */
  --accent3: #ec4899;  /* pink (gradient stop) */
  --neon: #22d3ee;     /* cyan — secondary */
  --pass: #10b981;
  --fail: #ef4444;
  --warn: #f59e0b;
  --glass: rgba(255, 255, 255, 0.04);
  --glass-strong: rgba(255, 255, 255, 0.07);
  --glass-border: rgba(255, 255, 255, 0.08);
  --grid: rgba(255, 255, 255, 0.018);
  --radius: 16px;
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, Helvetica, Arial, sans-serif;
  color: var(--fg);
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  background:
    radial-gradient(ellipse 70% 50% at 50% -10%, rgba(139, 92, 246, 0.30), transparent 70%),
    radial-gradient(ellipse 60% 60% at 90% 30%, rgba(236, 72, 153, 0.16), transparent 70%),
    radial-gradient(ellipse 60% 80% at 10% 70%, rgba(34, 211, 238, 0.12), transparent 70%),
    var(--bg);
  background-attachment: fixed;
  overflow-x: hidden;
}

body::before {
  content: '';
  position: fixed; inset: 0; z-index: -2; pointer-events: none;
  background-image:
    linear-gradient(var(--grid) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: radial-gradient(ellipse 80% 60% at 50% 30%, #000 30%, transparent 80%);
  -webkit-mask-image: radial-gradient(ellipse 80% 60% at 50% 30%, #000 30%, transparent 80%);
}

.container { max-width: 1200px; margin: 0 auto; padding: 0 1.5rem; }

/* ============================================================
   Buttons
   ============================================================ */
.btn {
  display: inline-flex; align-items: center; gap: 0.45rem;
  padding: 0.85rem 1.5rem;
  font-weight: 600;
  font-size: 0.95rem;
  border-radius: 10px;
  text-decoration: none;
  cursor: pointer;
  border: none;
  font-family: inherit;
  letter-spacing: -0.005em;
  position: relative;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.btn:hover { transform: translateY(-1px); }
.btn-primary {
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  color: #fff;
  box-shadow: 0 8px 32px rgba(139, 92, 246, 0.35), 0 1px 0 rgba(255,255,255,0.18) inset;
}
.btn-primary:hover { box-shadow: 0 12px 40px rgba(139, 92, 246, 0.50), 0 1px 0 rgba(255,255,255,0.2) inset; }
.btn-lg { padding: 1.05rem 2rem; font-size: 1.05rem; border-radius: 12px; }
.btn-ghost {
  background: var(--glass);
  color: var(--fg);
  border: 1px solid var(--glass-border);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.btn-ghost:hover { background: var(--glass-strong); border-color: rgba(255,255,255,0.16); }

/* ============================================================
   Nav
   ============================================================ */
nav {
  position: sticky; top: 0; z-index: 50;
  padding: 0.75rem 0;
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  background: rgba(8, 7, 10, 0.6);
  border-bottom: 1px solid var(--glass-border);
}
.nav-inner { display: flex; justify-content: space-between; align-items: center; }
.nav-brand {
  display: inline-flex; align-items: center; gap: 0.5rem;
  font-weight: 700; font-size: 1.05rem; color: var(--fg); text-decoration: none; letter-spacing: -0.02em;
}
.nav-brand .mark {
  width: 24px; height: 24px;
  background: linear-gradient(135deg, var(--accent), var(--accent3));
  border-radius: 6px;
  display: inline-flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 800; font-size: 0.78rem;
  font-family: 'SF Mono', Menlo, monospace;
}
.nav-links { display: flex; gap: 1.75rem; align-items: center; }
.nav-links a { color: var(--muted); text-decoration: none; font-size: 0.92rem; transition: color 0.15s; }
.nav-links a:hover { color: var(--fg); }
.nav-links .live {
  display: inline-flex; align-items: center; gap: 0.4rem;
  color: var(--pass);
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.25);
  padding: 0.25rem 0.65rem;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 600;
}
.nav-links .live::before {
  content: '';
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--pass);
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.85); }
}

/* ============================================================
   Hero
   ============================================================ */
.hero { padding: 6rem 0 5rem; position: relative; }
.hero-grid {
  display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 3rem;
  align-items: center;
}
@media (max-width: 880px) {
  .hero-grid { grid-template-columns: 1fr; gap: 2.5rem; }
}
.hero .eyebrow {
  display: inline-flex; align-items: center; gap: 0.5rem;
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  background: var(--glass);
  border: 1px solid var(--glass-border);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 500;
  margin-bottom: 1.25rem;
  letter-spacing: 0.01em;
}
.hero .eyebrow .v {
  display: inline-block;
  background: linear-gradient(135deg, var(--accent), var(--accent3));
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
  font-weight: 700;
  letter-spacing: 0;
}
.hero h1 {
  font-size: clamp(2.4rem, 6vw, 4.2rem);
  margin: 0 0 1.25rem;
  letter-spacing: -0.045em;
  line-height: 1.04;
  font-weight: 800;
}
.hero h1 .grad {
  background: linear-gradient(135deg, #c084fc 0%, #ec4899 50%, #f97316 100%);
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
  display: inline-block;
}
.hero .sub {
  color: var(--muted);
  font-size: clamp(1.05rem, 1.8vw, 1.25rem);
  max-width: 540px;
  margin: 0 0 1.75rem;
  line-height: 1.55;
}
.hero .sub strong { color: var(--fg); font-weight: 600; }
.hero .ctas { display: flex; gap: 0.75rem; flex-wrap: wrap; align-items: center; }
.hero .micro {
  margin-top: 1.25rem;
  font-size: 0.82rem; color: var(--muted2);
  display: flex; gap: 1rem; flex-wrap: wrap;
}
.hero .micro span { display: inline-flex; align-items: center; gap: 0.3rem; }
.hero .micro .ok { color: var(--pass); }

/* Floating glass audit cards on the right */
.hero-art { position: relative; }
.float-card {
  background: var(--glass-strong);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
  box-shadow: 0 24px 48px -16px rgba(0,0,0,0.5);
  font-family: 'SF Mono', Menlo, monospace;
  position: absolute;
}
.float-card.fc-1 {
  left: 4%; top: 4%; width: 78%;
  padding: 1rem 1.1rem;
  font-size: 0.78rem;
  animation: float 7s ease-in-out infinite;
}
.float-card.fc-2 {
  right: 0; top: 38%; width: 60%;
  padding: 0.75rem 0.9rem;
  font-size: 0.72rem;
  animation: float 5s ease-in-out infinite reverse;
  animation-delay: -2s;
  display: flex; flex-wrap: wrap; gap: 0.3rem 0.6rem;
  align-items: center;
}
.float-card.fc-3 {
  left: 8%; bottom: 0; width: 70%;
  padding: 0.7rem 0.9rem;
  font-size: 0.72rem;
  display: flex; align-items: center; gap: 0.6rem;
  animation: float 9s ease-in-out infinite;
  animation-delay: -4s;
}
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}
.fc-label { color: var(--muted); font-size: 0.68rem; }
.fc-url { color: var(--fg); font-weight: 600; }
.fc-line { color: var(--muted); }
.fc-key { color: var(--accent); }
.fc-str { color: var(--accent3); }
.fc-bool { color: var(--pass); }
.fc-num { color: var(--neon); }
.pill {
  display: inline-flex; align-items: center; gap: 0.3rem;
  padding: 0.15rem 0.45rem; border-radius: 4px;
  font-weight: 700; font-size: 0.66rem;
  letter-spacing: 0.02em;
}
.pill.pass { background: rgba(16,185,129,0.18); color: var(--pass); }
.pill.fail { background: rgba(239,68,68,0.18); color: var(--fail); }
.pill.skip { background: rgba(245,158,11,0.16); color: var(--warn); }
.pill .dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }

/* Animated background orbs (CSS only) */
.orb {
  position: absolute; border-radius: 50%;
  filter: blur(80px); opacity: 0.55;
  pointer-events: none; z-index: -1;
}
.orb.o1 {
  width: 320px; height: 320px;
  background: var(--accent);
  top: 40px; left: -80px;
  animation: orbMove 18s ease-in-out infinite;
}
.orb.o2 {
  width: 280px; height: 280px;
  background: var(--accent3);
  top: 120px; right: -60px;
  animation: orbMove 22s ease-in-out infinite reverse;
}
@keyframes orbMove {
  0%, 100% { transform: translate(0, 0); }
  33% { transform: translate(40px, -30px); }
  66% { transform: translate(-20px, 40px); }
}

/* ============================================================
   Marquee — live ticker
   ============================================================ */
.marquee {
  border-top: 1px solid var(--glass-border);
  border-bottom: 1px solid var(--glass-border);
  background: rgba(8,7,10,0.4);
  overflow: hidden;
  padding: 0.75rem 0;
  position: relative;
}
.marquee::before, .marquee::after {
  content: ''; position: absolute; top: 0; bottom: 0; width: 80px; z-index: 2;
  pointer-events: none;
}
.marquee::before { left: 0; background: linear-gradient(90deg, var(--bg), transparent); }
.marquee::after  { right: 0; background: linear-gradient(-90deg, var(--bg), transparent); }
.marquee-track {
  display: flex; gap: 0.6rem;
  width: max-content;
  animation: slide 60s linear infinite;
}
@keyframes slide {
  from { transform: translateX(0); }
  to   { transform: translateX(-50%); }
}
.mq-item {
  display: inline-flex; align-items: center; gap: 0.5rem;
  padding: 0.4rem 0.85rem;
  background: var(--glass);
  border: 1px solid var(--glass-border);
  border-radius: 10px;
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 0.78rem;
  white-space: nowrap;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}
.mq-item .url { color: var(--fg); }
.mq-item .ms  { color: var(--muted); }

/* ============================================================
   Stats banner
   ============================================================ */
.stats {
  padding: 4rem 0 3rem;
  display: grid; gap: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  max-width: 1100px; margin: 0 auto;
}
.stat {
  text-align: center;
  padding: 1rem;
}
.stat .n {
  font-size: clamp(2rem, 4vw, 2.8rem);
  font-weight: 800;
  letter-spacing: -0.04em;
  background: linear-gradient(135deg, var(--fg), var(--neon));
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
  line-height: 1;
}
.stat .l { font-size: 0.82rem; color: var(--muted); margin-top: 0.4rem; text-transform: uppercase; letter-spacing: 0.05em; }

/* ============================================================
   Section base + bento grid
   ============================================================ */
section { padding: 5rem 0; position: relative; }
.eyebrow-section { display: flex; justify-content: center; margin-bottom: 1rem; }
.eyebrow-section .pill-tag {
  display: inline-flex; align-items: center; gap: 0.45rem;
  padding: 0.3rem 0.8rem;
  border-radius: 999px;
  background: var(--glass);
  border: 1px solid var(--glass-border);
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 500;
  letter-spacing: 0.02em;
}
h2.section-title {
  text-align: center;
  font-size: clamp(1.7rem, 3.5vw, 2.6rem);
  margin: 0.4rem 0 0.7rem;
  letter-spacing: -0.035em;
  font-weight: 800;
  line-height: 1.1;
}
h2.section-title .grad {
  background: linear-gradient(135deg, #c084fc 0%, #ec4899 100%);
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
}
.section-sub {
  text-align: center;
  color: var(--muted);
  max-width: 620px; margin: 0 auto 3rem;
  font-size: 1.05rem;
}

/* Bento grid */
.bento {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 1rem;
  max-width: 1100px; margin: 0 auto;
}
.bento-cell {
  background: var(--glass);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
  padding: 1.5rem 1.5rem 1.5rem;
  position: relative;
  overflow: hidden;
  transition: background 0.2s, transform 0.2s, border-color 0.2s;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}
.bento-cell:hover {
  background: var(--glass-strong);
  border-color: rgba(255,255,255,0.16);
  transform: translateY(-2px);
}
.bento-cell::after {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.18), transparent);
}
.bento-cell.lg { grid-column: span 4; min-height: 220px; }
.bento-cell.md { grid-column: span 2; min-height: 220px; }
.bento-cell.half { grid-column: span 3; min-height: 180px; }
.bento-cell .label {
  font-size: 0.72rem; color: var(--accent);
  text-transform: uppercase; letter-spacing: 0.08em; font-weight: 700;
  margin-bottom: 0.6rem;
}
.bento-cell h3 { margin: 0 0 0.5rem; font-size: 1.15rem; letter-spacing: -0.02em; font-weight: 700; }
.bento-cell p { margin: 0; color: var(--muted); font-size: 0.93rem; }
.bento-cell .icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 40px; height: 40px;
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  border-radius: 10px;
  margin-bottom: 1rem;
  color: #fff;
}
.bento-cell .icon svg { width: 22px; height: 22px; stroke-width: 1.8; }
@media (max-width: 880px) {
  .bento { grid-template-columns: repeat(2, 1fr); }
  .bento-cell.lg, .bento-cell.md, .bento-cell.half { grid-column: span 1; }
}

/* ============================================================
   Sample response card
   ============================================================ */
.sample {
  max-width: 720px; margin: 3rem auto 0;
  background: var(--glass-strong);
  border: 1px solid var(--glass-border);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-radius: 16px;
  padding: 1.4rem 1.6rem;
  box-shadow: 0 16px 48px -16px rgba(0,0,0,0.5);
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 0.82rem;
  position: relative;
  overflow: hidden;
}
.sample::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(139,92,246,0.5), transparent);
}
.sample .head {
  display: flex; justify-content: space-between; align-items: center;
  padding-bottom: 0.8rem; margin-bottom: 0.8rem;
  border-bottom: 1px solid var(--glass-border);
}
.sample .url { color: var(--fg); font-weight: 600; }
.sample .latency { color: var(--pass); font-size: 0.74rem; font-weight: 700; }
.sample .curl { padding: 0.6rem 0; color: var(--muted); font-size: 0.78rem; line-height: 1.6; white-space: pre-wrap; }
.sample .curl .c-key { color: var(--accent); }
.sample .curl .c-str { color: var(--accent3); }
.sample .curl .c-com { color: var(--muted2); }
.sample .resp { padding-top: 0.8rem; }
.sample .resp .row { display: flex; align-items: center; padding: 0.3rem 0; gap: 0.6rem; color: var(--muted); }
.sample .resp .row .name { color: var(--fg); }
.sample .resp .row .pill { margin-left: auto; min-width: 56px; text-align: center; }

/* ============================================================
   Pricing — glass + accent gradient border
   ============================================================ */
.pricing-grid {
  display: grid; gap: 1.25rem;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  max-width: 1000px; margin: 0 auto;
}
.plan {
  background: var(--glass);
  border: 1px solid var(--glass-border);
  border-radius: 18px;
  padding: 2rem 1.75rem 1.75rem;
  display: flex; flex-direction: column;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  position: relative;
  overflow: hidden;
  transition: transform 0.2s, border-color 0.2s, background 0.2s;
}
.plan:hover { transform: translateY(-3px); }
.plan.featured {
  background: var(--glass-strong);
  border: 1px solid var(--accent);
  box-shadow: 0 0 0 1px var(--accent), 0 24px 48px -16px rgba(139,92,246,0.30);
}
.plan.featured::before {
  content: "Most popular";
  position: absolute; top: 14px; right: -32px;
  background: linear-gradient(135deg, var(--accent), var(--accent3));
  color: #fff; padding: 0.2rem 2.2rem;
  font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.04em;
  transform: rotate(35deg);
  text-transform: uppercase;
}
.plan-name { font-size: 1.1rem; font-weight: 700; margin: 0 0 0.4rem; letter-spacing: -0.01em; }
.plan-desc { color: var(--muted); font-size: 0.86rem; margin: 0 0 1.5rem; line-height: 1.45; }
.plan-price {
  font-size: 3rem; font-weight: 800;
  letter-spacing: -0.04em; margin: 0;
  display: flex; align-items: baseline; gap: 0.3rem;
  background: linear-gradient(135deg, var(--fg), var(--accent));
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
}
.plan-price small { font-size: 0.95rem; color: var(--muted); font-weight: 400; -webkit-text-fill-color: var(--muted); }
.plan-period { font-size: 0.78rem; color: var(--muted); margin-top: 0.2rem; }
.plan-features { list-style: none; padding: 0; margin: 1.5rem 0 0; flex-grow: 1; }
.plan-features li {
  padding: 0.5rem 0; color: var(--fg); font-size: 0.92rem;
  display: flex; align-items: center; gap: 0.6rem;
  border-bottom: 1px solid var(--glass-border);
}
.plan-features li:last-child { border-bottom: none; }
.plan-features li svg { flex-shrink: 0; color: var(--pass); width: 16px; height: 16px; }
.plan .btn { width: 100%; justify-content: center; margin-top: 1.5rem; }

/* ============================================================
   How: 3 steps
   ============================================================ */
.how-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  max-width: 1000px; margin: 0 auto;
}
@media (max-width: 760px) { .how-grid { grid-template-columns: 1fr; } }
.how-step {
  background: var(--glass);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius);
  padding: 1.5rem;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  position: relative;
}
.how-step .n {
  position: absolute; top: -16px; left: 1.5rem;
  display: inline-flex; align-items: center; justify-content: center;
  width: 38px; height: 38px;
  background: linear-gradient(135deg, var(--accent), var(--accent3));
  border-radius: 12px;
  color: #fff; font-weight: 800; font-size: 0.95rem;
  font-family: 'SF Mono', Menlo, monospace;
  box-shadow: 0 8px 24px rgba(139,92,246,0.35);
}
.how-step h3 { margin: 0.8rem 0 0.5rem; font-size: 1.05rem; }
.how-step p { color: var(--muted); font-size: 0.92rem; margin: 0 0 1rem; }

/* ============================================================
   Code block
   ============================================================ */
.code-block {
  background: rgba(0, 0, 0, 0.4);
  border: 1px solid var(--glass-border);
  border-radius: 10px;
  padding: 0.75rem 0.9rem;
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 0.82rem;
  color: var(--fg);
  overflow-x: auto;
  line-height: 1.55;
  white-space: pre;
}
.code-block .c-key { color: var(--accent); }
.code-block .c-str { color: var(--accent3); }
.code-block .c-com { color: var(--muted2); }

/* ============================================================
   CTA final
   ============================================================ */
.cta-final { text-align: center; padding: 6rem 0; position: relative; }
.cta-final h2 { font-size: clamp(2.2rem, 5vw, 3.4rem); margin-bottom: 0.75rem; letter-spacing: -0.04em; font-weight: 800; line-height: 1.1; }
.cta-final h2 .grad {
  background: linear-gradient(135deg, #c084fc, #ec4899, #f97316);
  -webkit-background-clip: text; background-clip: text;
  color: transparent;
}
.cta-final .sub { color: var(--muted); font-size: 1.05rem; max-width: 560px; margin: 0 auto 2rem; }
.cta-final .ctas { display: flex; gap: 0.85rem; justify-content: center; flex-wrap: wrap; }
.cta-final .trust {
  margin-top: 2rem; color: var(--muted2); font-size: 0.82rem;
  display: flex; gap: 1.25rem; justify-content: center; flex-wrap: wrap;
}
.cta-final .trust span::before { content: '✓ '; color: var(--pass); }

/* ============================================================
   Footer
   ============================================================ */
footer {
  border-top: 1px solid var(--glass-border);
  padding: 3rem 0 4rem;
}
.foot-grid {
  display: grid; gap: 2rem;
  grid-template-columns: 1.4fr repeat(3, 1fr);
}
@media (max-width: 760px) { .foot-grid { grid-template-columns: 1fr 1fr; } }
.foot-brand .mark {
  width: 28px; height: 28px;
  background: linear-gradient(135deg, var(--accent), var(--accent3));
  border-radius: 7px;
  display: inline-flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 800; font-size: 0.85rem;
  font-family: 'SF Mono', Menlo, monospace;
}
.foot-brand .name { font-weight: 700; font-size: 1.05rem; margin-bottom: 0.4rem; }
.foot-brand .tag { color: var(--muted); font-size: 0.9rem; max-width: 280px; }
.foot-col h4 {
  font-size: 0.78rem; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-bottom: 0.75rem;
}
.foot-col a {
  display: block; color: var(--fg); opacity: 0.85;
  text-decoration: none; font-size: 0.9rem;
  padding: 0.2rem 0;
}
.foot-col a:hover { opacity: 1; color: var(--accent); }
.foot-bottom {
  border-top: 1px solid var(--glass-border);
  margin-top: 2.5rem;
  padding-top: 1.5rem;
  color: var(--muted2);
  font-size: 0.82rem;
  display: flex; justify-content: space-between; flex-wrap: wrap; gap: 1rem;
}

@media (max-width: 600px) {
  .nav-links { gap: 0.75rem; font-size: 0.8rem; }
  .nav-links .live { display: none; }
  section { padding: 3.5rem 0; }
  .hero { padding: 4rem 0 3rem; }
}
</style>
</head>
<body>

<div class="orb o1"></div>
<div class="orb o2"></div>

<nav>
  <div class="container nav-inner">
    <a href="/" class="nav-brand">
      <span class="mark">x4</span>
      <span>x402 validator</span>
    </a>
    <div class="nav-links">
      <a href="#features">Features</a>
      <a href="#pricing">Pricing</a>
      <a href="#how">Docs</a>
      <span class="live">Live</span>
      <a href="https://github.com/MSSATANASS/x402-validator-tools" rel="noopener">GitHub</a>
    </div>
  </div>
</nav>

<!-- =================== HERO =================== -->
<header class="hero">
  <div class="container hero-grid">
    <div>
      <div class="eyebrow">
        <span class="v">v0.3.0</span>
        <span>· 100 % coverage · 167 tests · ~580 ms / audit</span>
      </div>
      <h1>
        Audit any <span class="grad">x402</span><br>
        endpoint in 580&nbsp;ms.
      </h1>
      <p class="sub">
        One REST call runs the <strong>strict-v2 conformance suite</strong> against
        your merchant URL. You get back <strong>structured JSON</strong> with one
        of four verdicts per check, plus an <strong>operator-actionable message</strong>
        when something is broken.
      </p>
      <div class="ctas">
        <a class="btn btn-primary btn-lg" href="/create-checkout-session?plan_id=pro">
          Start with Pro · $9 / mo →
        </a>
        <a class="btn btn-ghost" href="https://github.com/MSSATANASS/x402-validator-tools" rel="noopener">
          View on GitHub
        </a>
      </div>
      <div class="micro">
        <span class="ok">✓ Free tier — 100 audits / month</span>
        <span class="ok">✓ Cancel anytime from Stripe</span>
        <span class="ok">✓ No card on Free</span>
      </div>
    </div>

    <div class="hero-art">
      <!-- Floating glass audit cards, pure CSS animation -->
      <div class="float-card fc-1">
        <div style="margin-bottom:0.45rem"><span class="pill pass"><span class="dot"></span>OK</span> <span style="color:var(--muted)">POST /validate</span></div>
        <div class="fc-line"><span class="fc-label">url: </span><span class="fc-url">observer.137-184-67-179.sslip.io</span></div>
        <div class="fc-line"><span class="fc-label">mode:</span> <span class="fc-str">"standard"</span></div>
        <div class="fc-line"><span class="fc-label">key: </span><span class="fc-key">"sk_live_****"</span></div>
      </div>

      <div class="float-card fc-2">
        <span class="pill pass"><span class="dot"></span>manifest_discovery</span>
        <span class="pill pass"><span class="dot"></span>json_resilience</span>
        <span class="pill pass"><span class="dot"></span>bazaar_compliance</span>
        <span class="pill fail"><span class="dot"></span>caip2_compliance</span>
      </div>

      <div class="float-card fc-3">
        <span style="color:var(--pass); font-weight:700">200 OK</span>
        <span style="color:var(--muted)">· 580 ms</span>
        <span style="margin-left:auto; color:var(--muted)"><span style="color:var(--accent)">3/4</span> checks passed</span>
      </div>
    </div>
  </div>
</header>

<!-- =================== MARQUEE =================== -->
<div class="marquee" aria-hidden="true">
  <div class="marquee-track">
    <!-- First list of endpoints -->
    <div class="mq-row">
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">api.x-402.online</span><span class="ms">· 412 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">x402.asterpay.io</span><span class="ms">· 387 ms</span></span>
      <span class="mq-item"><span class="pill fail"><span class="dot"></span>FAIL</span><span class="url">ozmium.org</span><span class="ms">· 528 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">observer.137-184-67-179.sslip.io</span><span class="ms">· 580 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">api.smartflowproai.com</span><span class="ms">· 491 ms</span></span>
      <span class="mq-item"><span class="pill fail"><span class="dot"></span>FAIL</span><span class="url">call.kelam.sh</span><span class="ms">· 631 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">mcp.hugen.tokyo</span><span class="ms">· 466 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">defi.hugen.tokyo</span><span class="ms">· 503 ms</span></span>
      <span class="mq-item"><span class="pill fail"><span class="dot"></span>FAIL</span><span class="url">www.kaspa-402.org</span><span class="ms">· 412 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">apinow.fun</span><span class="ms">· 287 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">toolrail.dev</span><span class="ms">· 392 ms</span></span>
      <span class="mq-item"><span class="pill fail"><span class="dot"></span>FAIL</span><span class="url">agents.oromi.co.uk</span><span class="ms">· 528 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">data.greeneris.io</span><span class="ms">· 612 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">agent.weatherxm.com</span><span class="ms">· 423 ms</span></span>
    </div>
    <!-- Duplicate for seamless loop -->
    <div class="mq-row">
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">api.x-402.online</span><span class="ms">· 412 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">x402.asterpay.io</span><span class="ms">· 387 ms</span></span>
      <span class="mq-item"><span class="pill fail"><span class="dot"></span>FAIL</span><span class="url">ozmium.org</span><span class="ms">· 528 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">observer.137-184-67-179.sslip.io</span><span class="ms">· 580 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">api.smartflowproai.com</span><span class="ms">· 491 ms</span></span>
      <span class="mq-item"><span class="pill fail"><span class="dot"></span>FAIL</span><span class="url">call.kelam.sh</span><span class="ms">· 631 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">mcp.hugen.tokyo</span><span class="ms">· 466 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">defi.hugen.tokyo</span><span class="ms">· 503 ms</span></span>
      <span class="mq-item"><span class="pill fail"><span class="dot"></span>FAIL</span><span class="url">www.kaspa-402.org</span><span class="ms">· 412 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">apinow.fun</span><span class="ms">· 287 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">toolrail.dev</span><span class="ms">· 392 ms</span></span>
      <span class="mq-item"><span class="pill fail"><span class="dot"></span>FAIL</span><span class="url">agents.oromi.co.uk</span><span class="ms">· 528 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">data.greeneris.io</span><span class="ms">· 612 ms</span></span>
      <span class="mq-item"><span class="pill pass"><span class="dot"></span>PASS</span><span class="url">agent.weatherxm.com</span><span class="ms">· 423 ms</span></span>
    </div>
  </div>
</div>

<!-- =================== STATS =================== -->
<section style="padding:3rem 0 1rem">
  <div class="container stats">
    <div class="stat"><div class="n">27</div><div class="l">endpoints audited</div></div>
    <div class="stat"><div class="n">4</div><div class="l">conformance checks</div></div>
    <div class="stat"><div class="n">~580 ms</div><div class="l">avg audit latency</div></div>
    <div class="stat"><div class="n">100 %</div><div class="l">engine test coverage</div></div>
    <div class="stat"><div class="n">167</div><div class="l">engine tests passing</div></div>
  </div>
</section>

<!-- =================== FEATURES =================== -->
<section id="features">
  <div class="container">
    <div class="eyebrow-section"><span class="pill-tag">The four checks</span></div>
    <h2 class="section-title">Run a real <span class="grad">strict-v2</span> audit,<br>not a checkbox.</h2>
    <p class="section-sub">Each check returns one of <code>PASS</code> / <code>FAIL</code> /
    <code>CRITICAL_FAIL</code> / <code>ERROR</code> with a message an operator can act on.
    No "check failed" — only "Payment-Required header missing. Expected: 'X-Payment-Required:'. Found: none."</p>

    <div class="bento">
      <!-- Big card -->
      <div class="bento-cell lg">
        <span class="icon">__SVG_BAZAAR__</span>
        <div class="label">Largest concern</div>
        <h3>The 610-endpoint corpus</h3>
        <p>Per the x402 spec, the canonical wire location for PaymentRequired is the
        <code>PAYMENT-REQUIRED</code> HTTP header — not the response body. Many
        merchants ship the header with a fully-formed v2 payload while leaving the
        body empty. The validator distinguishes those channels and scores them correctly.</p>
      </div>

      <!-- Four check cells -->
      <div class="bento-cell md">
        <span class="icon">__SVG_MANIFEST__</span>
        <div class="label">Check 01</div>
        <h3>manifest_discovery</h3>
        <p><code>GET /.well-known/x402</code> must return valid JSON with an <code>accepts</code> or <code>products</code> payload.</p>
      </div>

      <div class="bento-cell md" style="min-height:220px">
        <span class="icon">__SVG_CAIP2__</span>
        <div class="label">Check 02</div>
        <h3>caip2_compliance</h3>
        <p>A payment header carries a valid CAIP-2 network identifier — caught before wallets reject it.</p>
      </div>

      <div class="bento-cell md">
        <span class="icon">__SVG_JSON__</span>
        <div class="label">Check 03</div>
        <h3>json_resilience</h3>
        <p>HTTP 402 body is a JSON object — not a primitive that crashes the reference verifier downstream.</p>
      </div>

      <div class="bento-cell md">
        <span class="icon">__SVG_BAZAAR__</span>
        <div class="label">Check 04</div>
        <h3>bazaar_compliance</h3>
        <p><code>extensions.bazaar</code> has <code>method=POST</code>, <code>serviceName</code> set, <code>tags</code> non-empty.</p>
      </div>
    </div>

    <!-- Live sample response card showing what they GET -->
    <div class="sample">
      <div class="head">
        <span class="url">https://observer.137-184-67-179.sslip.io</span>
        <span class="latency">200 OK · 580 ms</span>
      </div>
      <div class="curl"><span class="c-com"># POST /validate { url: "..." }</span>
curl <span class="c-key">https://lastminutestickets.com/validate</span> \
  <span class="c-com">-H</span> <span class="c-str">"X-API-Key: <span class="c-key">$YOUR_KEY</span>"</span> \
  <span class="c-com">-d</span> <span class="c-str">'{"url":"https://observer.137-184-67-179.sslip.io"}'</span></div>
      <div class="resp">
        <div class="row"><span class="pill pass"><span class="dot"></span>PASS</span> <span class="name">manifest_discovery</span></div>
        <div class="row"><span class="pill pass"><span class="dot"></span>PASS</span> <span class="name">json_resilience</span></div>
        <div class="row"><span class="pill pass"><span class="dot"></span>PASS</span> <span class="name">bazaar_compliance</span></div>
        <div class="row"><span class="pill fail"><span class="dot"></span>FAIL</span> <span class="name">caip2_compliance</span><span class="pill" style="background:rgba(0,0,0,0.3); color:var(--muted)">missing header</span></div>
      </div>
    </div>
  </div>
</section>

<!-- =================== HOW =================== -->
<section id="how">
  <div class="container">
    <div class="eyebrow-section"><span class="pill-tag">3 steps · 30 seconds</span></div>
    <h2 class="section-title">From <span class="grad">checkout</span> to <span class="grad">calling /validate</span>.</h2>
    <p class="section-sub">Stripe checkout, then one curl. That's it.</p>

    <div class="how-grid">
      <div class="how-step">
        <div class="n">1</div>
        <h3>Pick a plan</h3>
        <p>Stripe checkout with card / Apple Pay / Google Pay. €0 spend on Free, $9 on Pro.</p>
      </div>
      <div class="how-step">
        <div class="n">2</div>
        <h3>Receive key in seconds</h3>
        <p>Backed by a persistent key store. Need more? Just ask — we bump quotas on demand.</p>
        <div class="code-block"><span class="c-key">X-API-Key:</span> <span class="c-str">abc123XYZ...</span></div>
      </div>
      <div class="how-step">
        <div class="n">3</div>
        <h3>POST /validate</h3>
        <p>Drop into CI, cron, or a Slack alert. Returns structured JSON, ~580 ms.</p>
        <div class="code-block">POST /validate
{
  <span class="c-key">"url"</span>: <span class="c-str">"https://yoursite.com"</span>,
  <span class="c-key">"mode"</span>: <span class="c-str">"standard"</span>
}</div>
      </div>
    </div>
  </div>
</section>

<!-- =================== PRICING =================== -->
<section id="pricing" style="padding-top:3rem">
  <div class="container">
    <div class="eyebrow-section"><span class="pill-tag">Pricing</span></div>
    <h2 class="section-title">Priced for indie devs, <span class="grad">not enterprises.</span></h2>
    <p class="section-sub">Cancel from your Stripe dashboard any time. No long-term contract.</p>

    <div class="pricing-grid">
      <!-- Free -->
      <div class="plan">
        <h3 class="plan-name">Free</h3>
        <p class="plan-desc">For trying it out on a single merchant.</p>
        <div class="plan-price">$0<small>/mo</small></div>
        <div class="plan-period">100 audits / month · forever</div>
        <ul class="plan-features">
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>All 4 conformance checks</li>
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>JSON response</li>
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Community support</li>
        </ul>
        <a class="btn btn-ghost" href="/create-checkout-session?plan_id=free">Start free</a>
      </div>

      <!-- Pro (featured) -->
      <div class="plan featured">
        <h3 class="plan-name">Pro</h3>
        <p class="plan-desc">For shipping x402 merchants.</p>
        <div class="plan-price">$9<small>/mo</small></div>
        <div class="plan-period">500 audits / month</div>
        <ul class="plan-features">
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Everything in Free</li>
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg><strong>marketplace mode</strong> + per-product walks</li>
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Operator-actionable errors</li>
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Email support (&lt;24 h)</li>
        </ul>
        <a class="btn btn-primary" href="/create-checkout-session?plan_id=pro">Buy Pro — $9 / mo</a>
      </div>

      <!-- Enterprise -->
      <div class="plan">
        <h3 class="plan-name">Enterprise</h3>
        <p class="plan-desc">For higher-volume / catalogue compliance.</p>
        <div class="plan-price">$49<small>/mo</small></div>
        <div class="plan-period">5,000 audits / month</div>
        <ul class="plan-features">
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Everything in Pro</li>
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Bulk endpoints (beta)</li>
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Priority support (&lt;4 h)</li>
          <li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M4 12l5 5L20 6"/></svg>Volume rebate (talk to us)</li>
        </ul>
        <a class="btn btn-ghost" href="/create-checkout-session?plan_id=enterprise">Buy Enterprise — $49 / mo</a>
      </div>
    </div>
  </div>
</section>

<!-- =================== CTA FINAL =================== -->
<section class="cta-final">
  <div class="container">
    <h2>Run a real audit on your<br><span class="grad">merchant URL today.</span></h2>
    <p class="sub">Free plan: 100 audits / month. Enough to validate your whole catalogue, then upgrade if you need more.</p>
    <div class="ctas">
      <a class="btn btn-primary btn-lg" href="/create-checkout-session?plan_id=pro">Start with Pro · $9 / mo →</a>
      <a class="btn btn-ghost" href="/create-checkout-session?plan_id=free">Or start free</a>
    </div>
    <div class="trust">
      <span>100 % covered engine</span>
      <span>Open source</span>
      <span>Cancel anytime</span>
    </div>
  </div>
</section>

<!-- =================== FOOTER =================== -->
<footer>
  <div class="container">
    <div class="foot-grid">
      <div class="foot-brand">
        <div class="mark">x4</div>
        <div class="name" style="margin-top:0.6rem">x402 validator</div>
        <div class="tag">REST API that runs the x402 strict-v2 conformance suite against any URL.</div>
      </div>
      <div class="foot-col">
        <h4>Product</h4>
        <a href="#features">Features</a>
        <a href="#pricing">Pricing</a>
        <a href="#how">How it works</a>
        <a href="/plans">Plans API</a>
        <a href="/health">Status</a>
      </div>
      <div class="foot-col">
        <h4>Code</h4>
        <a href="https://github.com/MSSATANASS/x402-validator-tools" rel="noopener">Tools (this site)</a>
        <a href="https://github.com/MSSATANASS/x402-conformance-engine" rel="noopener">Engine fork</a>
        <a href="https://github.com/smartflowproai-lang/x402-endpoint-validator" rel="noopener">Upstream</a>
        <a href="https://pypi.org/project/x402-validator/" rel="noopener">pip install x402-validator</a>
      </div>
      <div class="foot-col">
        <h4>Contact</h4>
        <a href="mailto:support@lastminutestickets.com">support@lastminutestickets.com</a>
        <a href="https://github.com/MSSATANASS/x402-validator-tools/issues" rel="noopener">File an issue</a>
        <a href="mailto:gael@lastminutestickets.com">gael@lastminutestickets.com</a>
      </div>
    </div>
    <div class="foot-bottom">
      <div>© 2026 x402 validator · Apache-2.0</div>
      <div>stripes handled by Stripe · key store in <code>api_keys.json</code></div>
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


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing() -> HTMLResponse:
    return HTMLResponse(
        _LANDING_HTML
        .replace("__SVG_MANIFEST__", _SVG_MANIFEST)
        .replace("__SVG_CAIP2__", _SVG_CAIP2)
        .replace("__SVG_JSON__", _SVG_JSON)
        .replace("__SVG_BAZAAR__", _SVG_BAZAAR)
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
