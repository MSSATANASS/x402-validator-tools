# x402-validator-tools

Dashboard, API server, Stripe monetization, and PyPI packaging for [x402-validator](https://github.com/smartflowproai-lang/x402-endpoint-validator).

## What's here

| Component | File | Description |
|-----------|------|-------------|
| API Server | `api_server.py` | FastAPI — POST /validate, /create-checkout-session |
| Stripe | `stripe_integration.py` | Checkout sessions, webhooks, plan management |
| Dashboard | `dashboard_hosteado.py` | Flask — signup, API keys, validation history |
| Templates | `templates/` | HTML for signup/dashboard/landing |

## Deploy

```bash
export STRIPE_SECRET_KEY="sk_live_..."
export STRIPE_WEBHOOK_SECRET="whsec_..."
uvicorn api_server:app --host 0.0.0.0 --port 5000
```
