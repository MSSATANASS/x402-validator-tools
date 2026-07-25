import stripe
import os

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
stripe_webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

PLANS = {
    "pro": {"requests": 500, "price_id": "price_1Tx1OnPHIgVqi7nd5pNOrW7V"},
    "enterprise": {"requests": 5000, "price_id": "price_1Tx1OnPHIgVqi7ndsmtLf1P2"},
}


def create_checkout_session(plan_id: str, customer_email: str | None = None) -> str:
    plan = PLANS.get(plan_id)
    if not plan:
        raise ValueError(f"Unknown plan: {plan_id}")
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": plan["price_id"], "quantity": 1}],
        mode="subscription",
        customer_email=customer_email,
        success_url="https://cute-foxes-write.loca.lt/dashboard?session_id={CHECKOUT_SESSION_ID}",
        cancel_url="https://cute-foxes-write.loca.lt/pricing",
    )
    return session.url


def handle_webhook(payload: bytes, sig_header: str) -> dict:
    event = stripe.Webhook.construct_event(payload, sig_header, stripe_webhook_secret)
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_email", "unknown")
        subscription_id = session.get("subscription")
        return {"email": customer_email, "subscription_id": subscription_id, "status": "active"}
    if event["type"] == "invoice.payment_succeeded":
        return {"status": "payment_received"}
    if event["type"] == "customer.subscription.deleted":
        return {"status": "cancelled"}
    return {"status": "unhandled"}
