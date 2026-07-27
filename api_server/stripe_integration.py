"""Stripe integration for the x402 conformance API.

Only the checkout-session create / retrieve / webhook verify helpers live
here; the FastAPI app delegates everything else. Stripe is lazy-imported so
the package can be loaded in environments without a key (tests, CI).
"""

from __future__ import annotations

import os
from typing import Any, Optional

from api_server.models import PLANS, Plan


# Lazy Stripe import — keeps tests fast when no STRIPE_SECRET_KEY is set
_stripe = None


def _get_stripe():
    """Lazy import + configure ``stripe.api_key`` from env. Returns None if unconfigured."""
    global _stripe
    if _stripe is not None:
        return _stripe
    try:
        import stripe  # type: ignore
    except ImportError:
        return None

    api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not api_key:
        return None
    stripe.api_key = api_key
    _stripe = stripe
    return stripe


def is_configured() -> bool:
    """True if STRIPE_SECRET_KEY is set in the environment."""
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def create_checkout_session(
    plan_id: str,
    *,
    success_url: str,
    cancel_url: str,
) -> Optional[str]:
    """Create a Stripe Checkout session for ``plan_id`` and return its URL.

    Embeds ``metadata.plan_id`` so the webhook handler can route the paid
    checkout back to the correct tier without trusting the price.

    Returns ``None`` if the plan is free or Stripe is not configured.
    Raises ``ValueError`` for unknown plans.
    """
    if plan_id not in PLANS:
        raise ValueError(f"unknown plan: {plan_id!r}")

    plan: Plan = PLANS[plan_id]
    if plan.price_cents == 0 or plan.stripe_price_id is None:
        return None  # free plan — no checkout

    stripe = _get_stripe()
    if stripe is None:
        return None

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"plan_id": plan_id},
    )
    return session.url


def retrieve_session(session_id: str) -> Optional[dict[str, Any]]:
    """Fetch a checkout session by id and return a plain dict for our webhook handler.

    Returns ``None`` if Stripe is unconfigured or the lookup fails.
    """
    stripe = _get_stripe()
    if stripe is None:
        return None
    try:
        sess = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        return None
    return {
        "id": getattr(sess, "id", session_id),
        "customer": getattr(sess, "customer", None),
        "amount_total": getattr(sess, "amount_total", None),
        "subscription": getattr(sess, "subscription", None),
        "mode": getattr(sess, "mode", None),
        "metadata": dict(getattr(sess, "metadata", {}) or {}),
    }


def verify_webhook(payload: bytes, signature: str) -> Optional[dict]:
    """Verify a Stripe webhook signature and return the parsed event.

    Returns ``None`` if the secret is missing or the signature is invalid.
    """
    stripe = _get_stripe()
    if stripe is None:
        return None
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        return None
    try:
        event = stripe.Webhook.construct_event(payload, signature, secret)
        return event.to_dict() if hasattr(event, "to_dict") else dict(event)
    except Exception:
        return None
