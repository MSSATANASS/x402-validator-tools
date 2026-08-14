"""x402 v2 paywall helpers for this SaaS (challenge + optional facilitator).

Design
------
- Free discovery routes stay free (``security: []`` in OpenAPI).
- ``POST /validate`` is dual-access: valid ``X-API-Key`` **or** x402 payment.
- Without either, return HTTP 402 + ``PAYMENT-REQUIRED`` (base64 JSON v2).
- Settlement via facilitator is optional (``X402_FACILITATOR_URL``). Discovery
  registration only requires a valid 402 challenge shape.

Env
---
- ``X402_PAY_TO`` — required to enable the paywall (EVM address).
- ``X402_NETWORK`` — default ``eip155:8453`` (Base).
- ``X402_ASSET`` — default Base USDC.
- ``X402_AMOUNT_ATOMIC`` — default ``20000`` ($0.02 USDC, 6 decimals).
- ``X402_PRICE_USD`` — OpenAPI decimal price string, default ``0.02``.
- ``X402_FACILITATOR_URL`` — optional verify/settle base (no trailing slash).
- ``X402_MAX_TIMEOUT_SECONDS`` — default ``300``.
- ``PUBLIC_URL`` — resource URL base for challenges.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Base mainnet USDC
_DEFAULT_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
_DEFAULT_NETWORK = "eip155:8453"
_DEFAULT_AMOUNT = "20000"  # $0.02
_DEFAULT_PRICE_USD = "0.02"
_DEFAULT_TIMEOUT = 300

_EVM_ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


# Sentinel receive address when X402_PAY_TO is unset — still a valid 0x address
# so discovery probes parse accepts[]. Replace in production via X402_PAY_TO.
_DISCOVERY_PLACEHOLDER_PAY_TO = "0x4024024024024024024024024024024024024024"


def pay_to() -> str | None:
    """Configured receive address, or None if unset/invalid."""
    raw = (os.environ.get("X402_PAY_TO") or "").strip()
    if not raw or not _EVM_ADDR_RE.match(raw):
        return None
    return raw


def pay_to_for_challenge() -> str:
    """Address embedded in PaymentRequired accepts[] (always valid EVM format)."""
    return pay_to() or _DISCOVERY_PLACEHOLDER_PAY_TO


def paywall_enabled() -> bool:
    """Always on for /validate discovery + dual access.

    A missing X402_PAY_TO still emits 402 (placeholder payTo) so x402scan can
    register the route; real settlement requires a real payTo + facilitator.
    """
    return True


def network() -> str:
    return (os.environ.get("X402_NETWORK") or _DEFAULT_NETWORK).strip()


def asset() -> str:
    return (os.environ.get("X402_ASSET") or _DEFAULT_ASSET).strip()


def amount_atomic() -> str:
    return (os.environ.get("X402_AMOUNT_ATOMIC") or _DEFAULT_AMOUNT).strip()


def price_usd() -> str:
    return (os.environ.get("X402_PRICE_USD") or _DEFAULT_PRICE_USD).strip()


def max_timeout_seconds() -> int:
    try:
        return int(os.environ.get("X402_MAX_TIMEOUT_SECONDS") or _DEFAULT_TIMEOUT)
    except ValueError:
        return _DEFAULT_TIMEOUT


def facilitator_url() -> str | None:
    raw = (os.environ.get("X402_FACILITATOR_URL") or "").strip().rstrip("/")
    return raw or None


def public_base() -> str:
    return (
        os.environ.get("PUBLIC_URL") or "https://x402-validator-tools.fly.dev"
    ).rstrip("/")


def resource_url(path: str = "/validate") -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"{public_base()}{path}"


def build_payment_required(*, path: str = "/validate") -> dict[str, Any]:
    """Build an x402 v2 PaymentRequired object for the paid resource."""
    to = pay_to_for_challenge()
    configured = pay_to() is not None
    err = "Payment required to audit an x402 merchant URL"
    if not configured:
        err = (
            "Payment required — set X402_PAY_TO on the server to a Base USDC "
            "receive address before settling real payments"
        )
    return {
        "x402Version": 2,
        "error": err,
        "resource": {
            "url": resource_url(path),
            "description": (
                "Strict-v2 conformance audit: nine checks (manifest, CAIP-2, "
                "JSON resilience, Bazaar, bot-wall, accepts[], discovery, "
                "cold probe, batch-settlement) against any merchant URL."
            ),
            "mimeType": "application/json",
        },
        "accepts": [
            {
                "scheme": "exact",
                "network": network(),
                "asset": asset(),
                "amount": amount_atomic(),
                "payTo": to,
                "maxTimeoutSeconds": max_timeout_seconds(),
                "extra": {
                    "name": "USD Coin",
                    "version": "2",
                },
            }
        ],
    }


def encode_payment_required(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_b64_json(token: str) -> dict[str, Any] | None:
    try:
        padded = token + ("=" * (-len(token) % 4))
        try:
            raw = base64.b64decode(padded, validate=False)
        except Exception:
            raw = base64.urlsafe_b64decode(padded)
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def payment_required_response(*, path: str = "/validate") -> Response:
    """HTTP 402 with PAYMENT-REQUIRED header + JSON body (discovery-friendly)."""
    body = build_payment_required(path=path)
    encoded = encode_payment_required(body)
    return JSONResponse(
        status_code=402,
        content=body,
        headers={
            "PAYMENT-REQUIRED": encoded,
            # Legacy alias some clients still read
            "X-PAYMENT-REQUIRED": encoded,
            "Cache-Control": "no-store",
        },
    )


def extract_payment_signature(request: Request) -> str | None:
    """Return base64 payment payload from v2 or legacy headers, if any."""
    # Starlette headers are case-insensitive
    for name in (
        "payment-signature",
        "PAYMENT-SIGNATURE",
        "x-payment",
        "X-PAYMENT",
        "x-payment-signature",
    ):
        val = request.headers.get(name)
        if val and val.strip():
            return val.strip()
    return None


def extract_api_key(request: Request) -> str | None:
    for name in ("x-api-key", "X-API-Key"):
        val = request.headers.get(name)
        if val and val.strip():
            return val.strip()
    return None


async def verify_payment_with_facilitator(
    payment_b64: str,
    payment_required: dict[str, Any],
) -> tuple[bool, dict[str, Any] | None]:
    """Best-effort facilitator verify (+ settle if verify succeeds).

    Returns ``(ok, settlement_or_error)``. When no facilitator is configured,
    returns ``(False, {"error": "facilitator_not_configured"})`` so callers
    can fall back to API-key auth only.
    """
    base = facilitator_url()
    if not base:
        return False, {"error": "facilitator_not_configured"}

    payment = decode_b64_json(payment_b64)
    if payment is None:
        return False, {"error": "invalid_payment_signature_encoding"}

    payload = {
        "x402Version": payment_required.get("x402Version", 2),
        "paymentPayload": payment,
        "paymentRequirements": payment_required.get("accepts", [None])[0],
    }
    # Some facilitators want the full PaymentRequired envelope
    alt_payload = {
        "x402Version": payment_required.get("x402Version", 2),
        "paymentHeader": payment_b64,
        "paymentRequirements": payment_required,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        for body in (payload, alt_payload):
            try:
                r = await client.post(f"{base}/verify", json=body)
            except Exception as exc:
                return False, {"error": "facilitator_unreachable", "detail": str(exc)}
            if r.status_code >= 400:
                continue
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            is_valid = bool(
                data.get("isValid")
                or data.get("valid")
                or data.get("verified")
                or (isinstance(data, dict) and data.get("success") is True)
            )
            if not is_valid and r.status_code == 200 and not data:
                # empty 200 — treat as not verified
                continue
            if not is_valid:
                continue
            # Optional settle
            try:
                s = await client.post(f"{base}/settle", json=body)
                settle_body = (
                    s.json()
                    if s.headers.get("content-type", "").startswith("application/json")
                    else {"status_code": s.status_code}
                )
            except Exception as exc:
                settle_body = {"error": "settle_failed", "detail": str(exc)}
            return True, {"verify": data, "settle": settle_body}
        try:
            detail = r.json()  # type: ignore[name-defined]
        except Exception:
            detail = {"status_code": getattr(r, "status_code", None)}  # type: ignore[name-defined]
        return False, {"error": "facilitator_rejected", "detail": detail}


def openapi_payment_info() -> dict[str, Any]:
    """OpenAPI ``x-payment-info`` for discovery (decimal USD amount)."""
    return {
        "price": {
            "mode": "fixed",
            "currency": "USD",
            "amount": price_usd(),
        },
        "protocols": [
            {"x402": {}},
        ],
    }
