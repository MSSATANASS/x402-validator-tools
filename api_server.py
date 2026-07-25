from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import stripe
import os

app = FastAPI(title="x402 Validator API", version="0.1.0")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")

PLANS = {
    "free": {"requests": 100, "price": 0},
    "pro": {"requests": 500, "price": 900, "price_id": "price_1Tx1OnPHIgVqi7nd5pNOrW7V"},
    "enterprise": {"requests": 5000, "price": 4900, "price_id": "price_1Tx1OnPHIgVqi7ndsmtLf1P2"},
}

api_keys: dict[str, str] = {}  # key -> plan


class ValidateRequest(BaseModel):
    url: str
    mode: str = "standard"


class ValidateResponse(BaseModel):
    url: str
    overall: str
    checks: list[dict]
    latency_ms: float | None = None


@app.post("/validate", response_model=ValidateResponse)
async def validate(req: ValidateRequest, x_api_key: str = Header(...)):
    plan = api_keys.get(x_api_key)
    if not plan:
        raise HTTPException(401, "Invalid API key")
    from x402_validator.conformance import run_single_audit

    report = await run_single_audit(req.url, timeout=10.0, mode=req.mode)
    return ValidateResponse(
        url=report.target_url,
        overall=report.overall_status,
        checks=[c.model_dump() for c in report.checks],
        latency_ms=None,
    )


@app.post("/create-checkout-session")
async def create_checkout_session(plan_id: str):
    if plan_id not in PLANS:
        raise HTTPException(400, "Invalid plan")
    plan = PLANS[plan_id]
    if plan_id == "free":
        return {"note": "Free plan — no checkout needed"}
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": plan["price_id"], "quantity": 1}],
        mode="subscription",
        success_url="https://x402-validator-tools.onrender.com/success",
        cancel_url="https://x402-validator-tools.onrender.com/cancel",
    )
    return {"checkout_url": session.url}
