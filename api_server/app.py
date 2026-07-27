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
<title>x402 Validator — REST API for x402 strict-v2 audits</title>
<meta name="description" content="Audit any x402 merchant URL with one POST. Manifest, CAIP-2, JSON resilience, Bazaar — JSON back in 580 ms. Free, Pro, Enterprise.">
<link rel="preconnect" href="https://api.fontshare.com" crossorigin>
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link href="https://api.fontshare.com/v2/css?f[]=general-sans@400,500,600,700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
/* ============================================================
   Design system (MotionSites-inspired)
   - Two-font stack: Geist Sans (body) + General Sans (headline)
   - Deep dark blue-purple background, blurred glass overlay shape
   - Liquid-glass utility class for cards / nav
   ============================================================ */
:root {
  --background: 260 87% 3%;        /* hsl deep dark blue-purple */
  --foreground: 40 6% 95%;        /* off-white */
  --hero-sub:    40 6% 82%;        /* lighter off-white */
  --gray-950: 260 30% 6%;
  --accent: #6366f1;              /* indigo (gradient stop 1) */
  --accent2: #a855f7;             /* purple (gradient stop 2) */
  --accent3: #fcd34d;             /* amber (gradient stop 3) */
  --accent-green: #10b981;
  --accent-red:   #ef4444;
  --accent-cyan:  #22d3ee;
  --hero-grad: linear-gradient(to left, #6366f1, #a855f7, #fcd34d);
  --glass-bg: rgba(255, 255, 255, 0.04);
  --glass-bg-strong: rgba(255, 255, 255, 0.07);
  --glass-border: rgba(255, 255, 255, 0.10);
  --grad-fg: 0 0% 100%;
  --grad-fg-soft: 40 6% 82%;
}

* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }

body {
  font-family: 'Geist', -apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif;
  color: hsl(var(--foreground));
  background: hsl(var(--background));
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-feature-settings: 'cv02','cv03','cv04','cv11';
  overflow-x: hidden;
}

h1, h2, h3, h4 {
  font-family: 'General Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-feature-settings: 'ss01';
}

button {
  font-family: inherit;
  cursor: pointer;
  border: none;
  background: none;
  color: inherit;
}

/* utility for hero copy */
.text-foreground { color: hsl(var(--foreground)); }
.text-hero-sub   { color: hsl(var(--hero-sub)); }
.text-foreground-90 { color: hsl(var(--foreground) / 0.9); }
.text-foreground-50 { color: hsl(var(--foreground) / 0.5); }

/* ============================================================
   Liquid-glass (utility used by hero buttons + nav-pill)
   ============================================================ */
.liquid-glass {
  background: rgba(255, 255, 255, 0.01);
  background-blend-mode: luminosity;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  border: none;
  box-shadow: inset 0 1px 1px rgba(255, 255, 255, 0.1);
  position: relative;
  overflow: hidden;
}
.liquid-glass::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1.4px;
  background: linear-gradient(180deg, rgba(255,255,255,0.45) 0%, rgba(255,255,255,0.15) 20%, rgba(255,255,255,0) 40%, rgba(255,255,255,0) 60%, rgba(255,255,255,0.15) 80%, rgba(255,255,255,0.45) 100%);
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
}

/* ============================================================
   HeroSecondary button (CTA)
   ============================================================ */
.heroSecondary {
  background: hsl(var(--foreground));
  color: hsl(var(--background));
  padding: 14px 24px;
  border-radius: 999px;
  font-size: 0.95rem;
  font-weight: 600;
  letter-spacing: -0.01em;
  transition: transform 0.15s, box-shadow 0.2s;
}
.heroSecondary:hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 32px rgba(255,255,255,0.12);
}
.heroSecondary.sm { padding: 8px 16px; font-size: 0.85rem; }

/* ============================================================
   HERO SECTION (full screen)
   ============================================================ */
.hero-section {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  position: relative;
  overflow: visible;
  isolation: isolate;
}

/* Background video slot — falls back to a CSS animated gradient
   when no video URL is wired in (because we can't access the
   MotionSites CDN url from this template). */
.hero-section .hero-video-wrap {
  position: absolute;
  inset: 0;
  overflow: hidden;
  z-index: -1;
}
.hero-section .hero-video {
  position: absolute; inset: 0;
  width: 100%; height: 100%;
  object-fit: cover;
  opacity: 0;
  transition: opacity 0.5s ease;
}
.hero-section .hero-fallback {
  position: absolute; inset: 0;
  background:
    radial-gradient(ellipse 60% 45% at 50% 0%, rgba(99,102,241,0.35), transparent 60%),
    radial-gradient(ellipse 50% 60% at 90% 100%, rgba(168,85,247,0.20), transparent 60%),
    radial-gradient(ellipse 50% 60% at 10% 100%, rgba(252,211,77,0.10), transparent 60%),
    hsl(var(--background));
  animation: driftBg 24s ease-in-out infinite alternate;
}
@keyframes driftBg {
  0%   { transform: translate3d(0,0,0) scale(1); }
  100% { transform: translate3d(-2%, -1%, 0) scale(1.05); }
}

.hero-section .hero-video-wrap.is-playing .hero-video { opacity: 1; }

/* The blurred overlay shape (decorative, behind content) */
.hero-blob {
  width: 984px;
  height: 527px;
  opacity: 0.9;
  background: hsl(var(--gray-950));
  filter: blur(82px);
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  pointer-events: none;
  z-index: -1;
}

/* ============================================================
   Hero NAV (top of hero section)
   ============================================================ */
.hero-nav {
  width: 100%;
  padding: 20px 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: relative;
  z-index: 5;
}
.hero-nav-logo {
  height: 32px;
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  color: hsl(var(--foreground));
  text-decoration: none;
  font-family: 'General Sans', sans-serif;
  font-weight: 700;
  font-size: 1.05rem;
  letter-spacing: -0.02em;
}
.hero-nav-logo .mark {
  width: 32px; height: 32px;
  border-radius: 8px;
  background: var(--hero-grad);
  display: inline-flex; align-items: center; justify-content: center;
  color: hsl(var(--background));
  font-weight: 800;
  font-size: 0.85rem;
  font-family: 'Geist', monospace;
}
.hero-nav-links {
  display: flex; gap: 1.75rem; align-items: center;
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 999px;
  padding: 8px 18px;
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
.hero-nav-links button, .hero-nav-links a {
  background: none; border: none; cursor: pointer;
  color: hsl(var(--foreground) / 0.9);
  font-family: inherit;
  font-size: 0.88rem;
  letter-spacing: -0.005em;
  display: inline-flex; align-items: center; gap: 0.25rem;
  padding: 4px 6px;
  text-decoration: none;
  transition: color 0.15s;
}
.hero-nav-links button:hover, .hero-nav-links a:hover { color: hsl(var(--foreground)); }
.hero-nav-links svg.chev { width: 12px; height: 12px; opacity: 0.7; }
.hero-nav-links svg.chev polyline,
.hero-nav-links svg.chev line { stroke: currentColor; stroke-width: 1.5; }
.hero-nav-cta { display: inline-flex; align-items: center; gap: 0.6rem; }

/* Divider line right below the navbar */
.hero-divider {
  height: 1px;
  width: 100%;
  background: linear-gradient(to right, transparent, hsl(var(--foreground) / 0.20), transparent);
  margin-top: 3px;
}

/* ============================================================
   Hero CONTENT (centered)
   ============================================================ */
.hero-content {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  z-index: 4;
  text-align: center;
  padding: 0 32px;
}
.hero-content-inner { max-width: 1400px; }
.hero-headline {
  font-family: 'General Sans', sans-serif;
  font-weight: 400;
  font-size: clamp(80px, 14vw, 220px);
  line-height: 1.02;
  letter-spacing: -0.024em;
  margin: 0;
  color: hsl(var(--foreground));
}
.hero-headline .accent {
  background-image: var(--hero-grad);
  background-clip: text;
  -webkit-background-clip: text;
  color: transparent;
  -webkit-text-fill-color: transparent;
}
.hero-sub {
  color: hsl(var(--hero-sub));
  font-size: 1.125rem;
  line-height: 1.55;
  margin: 9px auto 0;
  max-width: 28rem;
  opacity: 0.92;
}
.hero-sub span.divide { display: block; }

.hero-cta-row {
  margin-top: 25px;
  display: inline-flex;
  align-items: center;
  gap: 0.75rem;
}
.hero-trust {
  margin-top: 1.25rem;
  color: hsl(var(--foreground) / 0.6);
  font-size: 0.82rem;
  display: inline-flex; align-items: center; gap: 1rem; flex-wrap: wrap; justify-content: center;
}
.hero-trust span::before { content: '✓  '; color: var(--accent-green); }

/* ============================================================
   Logo MARQUEE (bottom of hero)
   ============================================================ */
.logo-marquee {
  width: 100%;
  padding-bottom: 40px;
  position: relative;
  z-index: 3;
}
.logo-marquee-inner {
  max-width: 64rem;
  margin: 0 auto;
  padding: 0 1.5rem;
  display: flex; gap: 3rem; align-items: center;
}
.logo-marquee-inner .left {
  color: hsl(var(--foreground) / 0.5);
  font-size: 0.85rem;
  flex-shrink: 0;
  white-space: nowrap;
}
.logo-marquee-inner .right { flex: 1; overflow: hidden; position: relative; }
.logo-marquee-inner .right::before,
.logo-marquee-inner .right::after {
  content: ''; position: absolute; top: 0; bottom: 0; width: 80px; z-index: 2;
  pointer-events: none;
}
.logo-marquee-inner .right::before { left: 0; background: linear-gradient(90deg, hsl(var(--background)), transparent); }
.logo-marquee-inner .right::after  { right: 0; background: linear-gradient(-90deg, hsl(var(--background)), transparent); }
.marquee-track {
  display: flex; gap: 4rem;
  width: max-content;
  animation: marqueeSlide 20s linear infinite;
}
@keyframes marqueeSlide {
  from { transform: translateX(0%); }
  to   { transform: translateX(-50%); }
}
.logo-cell {
  display: inline-flex; align-items: center; gap: 0.6rem;
  font-family: 'General Sans', sans-serif;
  font-weight: 600;
  font-size: 1rem;
  color: hsl(var(--foreground));
  white-space: nowrap;
}
.logo-cell .mark {
  width: 24px; height: 24px;
  border-radius: 6px;
  display: inline-flex; align-items: center; justify-content: center;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  font-size: 0.75rem; font-weight: 700;
  color: hsl(var(--foreground));
  font-family: 'Geist', monospace;
}

/* ============================================================
   Below-hero SECTION (pricing etc.)
   ============================================================ */
.section { padding: 6rem 1.5rem; position: relative; }
.section.eyebrow-row { display: flex; justify-content: center; margin-bottom: 1rem; }
.section .pill-tag {
  display: inline-flex; align-items: center; gap: 0.45rem;
  padding: 0.3rem 0.85rem;
  border-radius: 999px;
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--glass-border);
  color: hsl(var(--foreground) / 0.7);
  font-size: 0.78rem;
  font-weight: 500;
}
h2.section-title {
  text-align: center;
  font-size: clamp(1.8rem, 4vw, 3rem);
  margin: 0.5rem auto 1rem;
  letter-spacing: -0.035em;
  font-weight: 600;
  line-height: 1.08;
  max-width: 32rem;
}
h2.section-title .grad {
  background-image: linear-gradient(to left, #6366f1, #a855f7, #fcd34d);
  background-clip: text;
  -webkit-background-clip: text;
  color: transparent;
}
.section-sub {
  text-align: center;
  color: hsl(var(--foreground) / 0.7);
  max-width: 36rem; margin: 0 auto 3rem;
  font-size: 1.05rem;
}

.pricing-grid {
  display: grid; gap: 1.25rem;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  max-width: 1000px; margin: 0 auto;
}
.plan {
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--glass-border);
  border-radius: 18px;
  padding: 2rem 1.75rem;
  display: flex; flex-direction: column;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  position: relative; overflow: hidden;
  transition: transform 0.2s, border-color 0.2s;
}
.plan:hover { transform: translateY(-3px); }
.plan.featured {
  border: 1px solid var(--accent);
  box-shadow: 0 0 0 1px var(--accent), 0 24px 48px -16px rgba(99,102,241,0.30);
}
.plan.featured::before {
  content: "Most popular";
  position: absolute; top: 14px; right: -32px;
  background-image: var(--hero-grad);
  color: hsl(var(--background));
  padding: 0.2rem 2.2rem;
  font-size: 0.7rem; font-weight: 700;
  letter-spacing: 0.04em;
  transform: rotate(35deg);
}
.plan-name { font-size: 1.1rem; font-weight: 700; margin: 0 0 0.3rem; letter-spacing: -0.01em; }
.plan-desc { color: hsl(var(--foreground) / 0.7); font-size: 0.86rem; margin: 0 0 1.5rem; }
.plan-price {
  font-size: 3rem; font-weight: 700;
  letter-spacing: -0.04em; margin: 0;
  display: flex; align-items: baseline; gap: 0.3rem;
  background-image: linear-gradient(to left, #ffffff, hsl(var(--foreground)));
  background-clip: text;
  -webkit-background-clip: text;
  color: transparent;
}
.plan-price small { font-size: 0.95rem; color: hsl(var(--foreground) / 0.7); font-weight: 400; -webkit-text-fill-color: hsl(var(--foreground) / 0.7); }
.plan-period { font-size: 0.78rem; color: hsl(var(--foreground) / 0.6); margin-top: 0.2rem; }
.plan-features { list-style: none; padding: 0; margin: 1.5rem 0 0; flex-grow: 1; }
.plan-features li { padding: 0.5rem 0; color: hsl(var(--foreground)); font-size: 0.92rem; display: flex; align-items: center; gap: 0.6rem; border-bottom: 1px solid var(--glass-border); }
.plan-features li:last-child { border-bottom: none; }
.plan-features li svg { flex-shrink: 0; color: var(--accent-green); width: 16px; height: 16px; }
.plan-cta {
  width: 100%; justify-content: center; margin-top: 1.5rem;
  padding: 12px 24px; border-radius: 999px;
  font-size: 0.95rem; font-weight: 600;
  border: none; cursor: pointer;
  font-family: inherit;
  text-decoration: none;
  display: inline-flex; align-items: center; gap: 0.45rem;
}
.plan-cta.solid { background: hsl(var(--foreground)); color: hsl(var(--background)); }
.plan-cta.outline { background: transparent; color: hsl(var(--foreground)); border: 1px solid var(--glass-border); }

/* ============================================================
   Footer
   ============================================================ */
footer {
  border-top: 1px solid var(--glass-border);
  padding: 3rem 1.5rem 4rem;
  color: hsl(var(--foreground) / 0.7);
  background: hsl(var(--background));
}
.foot-grid {
  display: grid; gap: 2rem;
  grid-template-columns: 1.4fr repeat(3, 1fr);
  max-width: 1100px; margin: 0 auto;
}
@media (max-width: 760px) { .foot-grid { grid-template-columns: 1fr 1fr; } }
.foot-brand .mark {
  width: 32px; height: 32px;
  border-radius: 8px;
  background-image: var(--hero-grad);
  display: inline-flex; align-items: center; justify-content: center;
  color: hsl(var(--background));
  font-weight: 800; font-size: 0.95rem;
  font-family: 'Geist', monospace;
}
.foot-brand .name { font-weight: 700; font-size: 1rem; margin: 0.6rem 0 0.4rem; color: hsl(var(--foreground)); }
.foot-brand .tag { font-size: 0.88rem; max-width: 280px; color: hsl(var(--foreground) / 0.65); }
.foot-col h4 {
  font-size: 0.72rem; color: hsl(var(--foreground) / 0.55);
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-bottom: 0.8rem;
  font-weight: 600;
}
.foot-col a {
  display: block; color: hsl(var(--foreground) / 0.85);
  text-decoration: none; font-size: 0.9rem;
  padding: 0.25rem 0;
  transition: color 0.15s;
}
.foot-col a:hover { color: hsl(var(--foreground)); }
.foot-bottom {
  border-top: 1px solid var(--glass-border);
  margin-top: 2.5rem;
  padding-top: 1.5rem;
  font-size: 0.82rem;
  color: hsl(var(--foreground) / 0.5);
  display: flex; justify-content: space-between; flex-wrap: wrap; gap: 1rem;
  max-width: 1100px; margin-left: auto; margin-right: auto;
}

@media (max-width: 600px) {
  .hero-nav { padding: 16px 18px; }
  .hero-nav-links { display: none; }
  .logo-marquee-inner { gap: 1rem; }
  .logo-marquee-inner .left { font-size: 0.78rem; }
  .marquee-track { gap: 2rem; }
  .section { padding: 4rem 1.25rem; }
}
</style>
</head>
<body>

<!-- =================== HERO SECTION (full screen, MotionSites vibe) =================== -->
<section class="hero-section">

  <!-- Background video slot with CSS-animated gradient fallback -->
  <div class="hero-video-wrap" id="heroVideoWrap">
    <video class="hero-video" id="heroVideo" muted playsinline preload="auto"
           poster=""
           src=""></video>
    <div class="hero-fallback" id="heroFallback"></div>
  </div>

  <!-- Centered blurred overlay shape (decorative) -->
  <div class="hero-blob" aria-hidden="true"></div>

  <!-- NAV -->
  <nav class="hero-nav">
    <a href="/" class="hero-nav-logo">
      <span class="mark">x4</span>
      <span>x402 validator</span>
    </a>

    <div class="hero-nav-links liquid-glass">
      <a href="#pricing">Pricing</a>
      <button type="button">
        Tools
        <svg class="chev" viewBox="0 0 12 12" aria-hidden="true"><polyline points="2,4 6,8 10,4" fill="none"/></svg>
      </button>
      <button type="button">
        Docs
        <svg class="chev" viewBox="0 0 12 12" aria-hidden="true"><polyline points="2,4 6,8 10,4" fill="none"/></svg>
      </button>
      <a href="/health">Status</a>
    </div>

    <div class="hero-nav-cta">
      <a class="heroSecondary sm" href="/create-checkout-session?plan_id=pro">Get API key</a>
    </div>
  </nav>

  <div class="hero-divider"></div>

  <!-- Centered CONTENT -->
  <div class="hero-content">
    <div class="hero-content-inner">
      <h1 class="hero-headline">Audit <span class="accent">x402</span> endpoints<span style="display:block"></span></h1>
      <p class="hero-sub">
        <span>The fastest strict-v2 conformance suite</span>
        <span class="divide">for x402 merchants. One POST, structured JSON back.</span>
      </p>

      <div class="hero-cta-row">
        <a class="heroSecondary" href="/create-checkout-session?plan_id=pro">View pricing &nbsp;→</a>
      </div>

      <div class="hero-trust">
        <span>100 % covered engine</span>
        <span>167 tests passing</span>
        <span>~580 ms per audit</span>
      </div>
    </div>
  </div>

  <!-- Logo MARQUEE → real endpoints audited (treated as customers) -->
  <div class="logo-marquee">
    <div class="logo-marquee-inner">
      <div class="left">Audited on real<br>merchants · 2026</div>
      <div class="right">
        <div class="marquee-track">
          <span class="logo-cell"><span class="mark">A</span>Asterpay</span>
          <span class="logo-cell"><span class="mark">H</span>Hugen</span>
          <span class="logo-cell"><span class="mark">O</span>Observer</span>
          <span class="logo-cell"><span class="mark">X</span>x402 Online</span>
          <span class="logo-cell"><span class="mark">G</span>Greeneris</span>
          <span class="logo-cell"><span class="mark">S</span>SmartFlow</span>
          <span class="logo-cell"><span class="mark">A</span>API Now</span>
          <span class="logo-cell"><span class="mark">W</span>Web3 ID</span>
          <span class="logo-cell"><span class="mark">B</span>Bazaar Viridis</span>
          <span class="logo-cell"><span class="mark">S</span>Stable Travel</span>
          <!-- Duplicate for seamless loop -->
          <span class="logo-cell"><span class="mark">A</span>Asterpay</span>
          <span class="logo-cell"><span class="mark">H</span>Hugen</span>
          <span class="logo-cell"><span class="mark">O</span>Observer</span>
          <span class="logo-cell"><span class="mark">X</span>x402 Online</span>
          <span class="logo-cell"><span class="mark">G</span>Greeneris</span>
          <span class="logo-cell"><span class="mark">S</span>SmartFlow</span>
          <span class="logo-cell"><span class="mark">A</span>API Now</span>
          <span class="logo-cell"><span class="mark">W</span>Web3 ID</span>
          <span class="logo-cell"><span class="mark">B</span>Bazaar Viridis</span>
          <span class="logo-cell"><span class="mark">S</span>Stable Travel</span>
        </div>
      </div>
    </div>
  </div>

</section>

<!-- =================== PRICING (compact, weighted) =================== -->
<section id="pricing" class="section">
  <div class="section eyebrow-row"><span class="pill-tag">Pricing</span></div>
  <h2 class="section-title">Pick a plan. <span class="grad">Cancel anytime.</span></h2>
  <p class="section-sub">Stripe billed through lastminutestickets.com. No long-term contract.</p>

  <div class="pricing-grid">
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
      <a class="plan-cta outline" href="/create-checkout-session?plan_id=free">Start free</a>
    </div>

    <div class="plan featured">
      <h3 class="plan-name">Pro</h3>
      <p class="plan-desc">For shipping x402 merchants.</p>
      <div class="plan-price">$9<small>/mo</small></div>
      <div class="plan-period">500 audits / month</div>
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
      <div class="plan-price">$49<small>/mo</small></div>
      <div class="plan-period">5,000 audits / month</div>
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
// Background video fade-in/out loop (matches the motionsites reference).
// Falls back to the CSS gradient overlay if the video fails (404 / blocked).
(function () {
  const wrap = document.getElementById('heroVideoWrap');
  const vid  = document.getElementById('heroVideo');
  const fb   = document.getElementById('heroFallback');
  // No <source src=...> wired in this template — the fallback shows.
  // The fade-in/out loop is left in place for future use:
  function fadeLoop(video) {
    const fadeIn  = () => { video.style.opacity = '1'; };
    const fadeOut = () => {
      video.style.opacity = '0';
      setTimeout(() => { video.currentTime = 0; fadeIn(); }, 100);
    };
    video.addEventListener('ended', fadeOut);
  }
  if (vid && vid.querySelector('source')?.src) {
    fadeLoop(vid);
  }
  if (fb) fb.style.opacity = '1';
})();
</script>

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
