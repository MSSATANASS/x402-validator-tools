"""x402-validator-tools: complementary tools around the x402 conformance engine.

Layout:
    api_server/   — FastAPI server exposing audit as a paid API (Stripe)
    dashboard/    — Flask dashboard for browsing past audits
    proxy/        — aiohttp middleware that validates proxied traffic

The core engine lives in the ``x402-validator`` package; this monorepo only
contains the surrounding machinery.
"""

__version__ = "0.3.0"
