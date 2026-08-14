"""Pydantic models for the x402 conformance API.

These models are the public response shape. The internal AuditReport from
``x402_conformance_suite`` is flattened into a JSON-friendly form here.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Shared enums / constants
# ---------------------------------------------------------------------------

AuditMode = Literal["standard", "marketplace"]
PlanId = Literal["free", "pro", "enterprise"]
PLAN_IDS: tuple[str, ...] = ("free", "pro", "enterprise")
MAX_AUDIT_URL_LEN = 2048

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
    """Client request: audit a single merchant URL (``/validate``, ``/audit-public``).

    Strict validation:
    - ``url`` must be absolute ``http``/``https`` with a host (max 2048 chars)
    - ``mode`` is only ``standard`` or ``marketplace``
    - unknown JSON fields are rejected (``extra=forbid``)
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(
        ...,
        min_length=8,
        max_length=MAX_AUDIT_URL_LEN,
        description="Target merchant URL to audit (http or https)",
        examples=["https://merchant.example.com/pay"],
    )
    mode: AuditMode = Field(
        default="standard",
        description="standard | marketplace",
    )
    advise: bool = Field(
        default=False,
        description="Attach Inception AI remediation advice (requires INCEPTION_API_KEY server-side)",
    )
    explain: bool = Field(
        default=False,
        description="Attach a plain-language AI summary of the result for non-experts (requires INCEPTION_API_KEY server-side)",
    )

    @field_validator("url")
    @classmethod
    def url_must_be_http_with_host(cls, v: str) -> str:
        raw = (v or "").strip()
        if not raw:
            raise ValueError("url is required")
        if any(ch.isspace() for ch in raw):
            raise ValueError("url must not contain whitespace")
        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("url must use http or https scheme")
        if not parsed.netloc:
            raise ValueError("url must include a host")
        # Reject obviously non-network schemes that urlparse might still parse
        host = parsed.hostname or ""
        if not host:
            raise ValueError("url must include a host")
        return raw


class IssueKeyRequest(BaseModel):
    """Admin request to mint an API key."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    plan_id: PlanId = Field(..., description="free | pro | enterprise")


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
