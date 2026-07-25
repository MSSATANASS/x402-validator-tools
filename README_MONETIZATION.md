# Monetization Plan — x402-validator

## What We Monetize

| Tier | Price | Requests/mo | Features |
|------|-------|-------------|----------|
| Free | $0 | 100 | Basic validation, single URL |
| Pro | $9 | 500 | Batch audit, CSV/JSON/HTML export |
| Enterprise | $49 | 5,000 | API access, priority support, webhooks |

## Products

1. **Hosted API** — `POST /validate` returns full x402 audit report
2. **Hosted Dashboard** — `lastminutestickets.com` with signup, API key management, validation history, endpoint change alerts

## Timeline

- **Q3 2026**: Hosted API launch (Stripe billing, 100 free requests)
- **Q4 2026**: Hosted dashboard launch (user accounts, history, alerts)

## Stripe Integration

- Plans: `price_pro_monthly`, `price_enterprise_monthly`
- Webhook endpoint handles: checkout.session.completed, invoice.payment_succeeded, customer.subscription.deleted
- API keys auto-generated on signup, rate-limited per plan

## Deployment

- API & Dashboard: Docker Compose on `lastminutestickets.com`
- Stripe keys: set via environment variables
- Database: SQLite → PostgreSQL when scaling

## Notes

- NO marketing until 50+ PyPI downloads AND Tom merges PRs
- First customers: email the 50 PyPI downloaders directly
- Pricing may change based on feedback
