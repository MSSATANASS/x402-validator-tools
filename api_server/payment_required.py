"""Decode x402 PaymentRequired from HTTP body and/or headers.

Precedence (first successful object wins; sources are not merged):
  1. Body JSON object
  2. Header ``payment-required`` (base64 JSON)
  3. Header ``x-payment-required`` (base64 JSON)

Header names are matched case-insensitively (HTTP standard): keys are
lowercased before lookup.
"""
from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any


def _lower_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _b64_to_obj(token: str) -> dict[str, Any] | None:
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


def _body_to_obj(body: str | bytes | None) -> dict[str, Any] | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:
            return None
    text = body.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def decode_payment_required(
    *,
    body: str | bytes | None,
    headers: Mapping[str, str] | None,
) -> dict[str, Any] | None:
    """Return decoded PaymentRequired object or None if none of the sources work."""
    obj = _body_to_obj(body)
    if obj is not None:
        return obj
    h = _lower_headers(headers)
    for key in ("payment-required", "x-payment-required"):
        raw = h.get(key)
        if not raw:
            continue
        obj = _b64_to_obj(raw)
        if obj is not None:
            return obj
    return None


def decode_from_httpx_response(response) -> dict[str, Any] | None:
    """Convenience wrapper for httpx.Response."""
    return decode_payment_required(body=response.text, headers=response.headers)
