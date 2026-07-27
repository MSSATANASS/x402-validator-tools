"""FastAPI app exposing x402-validator as a paid API.

Endpoints
---------

    GET  /health          - liveness check (always 200 OK)
    GET  /plans           - list available subscription plans
    POST /validate        - audit a single URL (requires API key)
    POST /create-checkout-session - create a Stripe checkout session for a plan
    POST /stripe-webhook  - Stripe webhook receiver (signature verified)

API keys are issued out of band (the dashboard or admin CLI) and stored in
memory. For production, swap :data:`api_keys` for a database.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
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


# ---------------------------------------------------------------------------
# App + state
# ---------------------------------------------------------------------------


app = FastAPI(
    title="x402 Validator API",
    version="0.3.0",
    description="Audit x402 endpoint conformance as a service.",
)


# key -> plan_id
api_keys: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_audit(url: str, mode: str, timeout: float = 10.0):
    """Run the x402 audit and return the report. Raises if the engine is unreachable."""
    from x402_validator._engine import run_audit  # imported lazily so import errors
    return await run_audit(url, timeout=timeout, mode=mode)


def _require_api_key(x_api_key: str = Header(...)) -> str:
    """FastAPI dependency: 401 unless the supplied key is registered."""
    plan = api_keys.get(x_api_key)
    if not plan:
        raise HTTPException(401, "Invalid API key")
    return plan


def _flatten_checks(report) -> list[CheckResultItem]:
    """Convert internal check objects to API-friendly items."""
    return [
        CheckResultItem(
            name=c.check_name,
            status=c.status,
            message=c.message,
            details=c.details,
        )
        for c in report.checks
    ]


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

    base = os.environ.get("PUBLIC_URL", "https://x402-validator-tools.onrender.com")
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

    Currently logs the event type; production deployments persist events
    and reconcile customer ↔ API key here.
    """
    event = stripe_integration.verify_webhook(body, signature)
    if event is None:
        raise HTTPException(400, "Invalid signature or Stripe not configured")
    return {"received": True, "type": event.get("type")}


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
