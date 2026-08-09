"""Pydantic models for the x402 conformance API.

These models are the public response shape. The internal AuditReport from
``x402_conformance_suite`` is flattened into a JSON-friendly form here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Plan catalog (single source of truth)
# ---------------------------------------------------------------------------


class Plan(BaseModel):
    """A subscription plan exposed by the API."""

    id: str
    name: str
    requests_per_month: int
    price_cents: int
    stripe_price_id: str | None = None


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        requests_per_month=100,
        price_cents=0,
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        requests_per_month=500,
        price_cents=900,
        stripe_price_id="price_1Tx1OnPHIgVqi7nd5pNOrW7V",
    ),
    "enterprise": Plan(
        id="enterprise",
        name="Enterprise",
        requests_per_month=5000,
        price_cents=4900,
        stripe_price_id="price_1Tx1OnPHIgVqi7ndsmtLf1P2",
    ),
}


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------


class ValidateRequest(BaseModel):
    """Client request: audit a single URL."""

    url: str = Field(..., description="Target base URL to audit")
    mode: str = Field(
        default="standard",
        description="standard | marketplace",
    )
    advise: bool = Field(
        default=False,
        description="Attach Qwen AI remediation advice (requires DASHSCOPE_API_KEY server-side)",
    )
    explain: bool = Field(
        default=False,
        description="Attach a plain-language AI summary of the result for non-experts (requires DASHSCOPE_API_KEY server-side)",
    )


class CheckResultItem(BaseModel):
    """One check result, flattened for JSON consumers."""

    name: str
    status: str
    message: str
    details: dict | None = None


class ValidateResponse(BaseModel):
    """Audit response."""

    url: str
    overall: str
    summary: str
    checks: list[CheckResultItem]
    latency_ms: float | None = None
    timestamp: str
    ai_advice: str | None = None
    ai_summary: str | None = None


class CheckoutResponse(BaseModel):
    """Stripe checkout session creation response."""

    checkout_url: str | None = None
    note: str | None = None
    plan_id: str
