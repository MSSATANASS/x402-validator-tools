"""x402-validator-tools: complementary tools around the x402 conformance engine.

Layout:
    api_server/   — FastAPI server exposing audit as a paid API (Stripe)
    dashboard/    — Flask dashboard for browsing past audits
    proxy/        — aiohttp middleware that validates proxied traffic

The core engine lives in the ``x402-conformance-suite`` package; this monorepo only
contains the surrounding machinery.

Re-exports ``app`` so ``uvicorn api_server:app`` keeps working with the
pre-restructure Render start command (which was set before this package split).
"""

from api_server.app import app

__all__ = ["app", "main"]
__version__ = "0.3.0"
