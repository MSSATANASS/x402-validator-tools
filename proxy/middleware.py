"""aiohttp middleware that validates x402 conformance on proxied traffic.

A request to ``/forward/<host>/<path>`` is proxied to ``https://<host>/<path>``
and validated against the x402 conformance engine. The validation result is
attached as headers (``X-Validation-Status``, ``X-Validation-Report``) on
every response, regardless of pass/fail.

If the engine reports ``FAIL`` or ``CRITICAL_FAIL``, the response body is
replaced with HTTP 402 + a JSON error envelope that includes the validation
result. Operators can opt out of this rewrite via the config flag
``on_fail: pass_through``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import aiohttp
import yaml
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)
log = logging.getLogger("x402-proxy")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class ProxyConfig:
    listen_host: str = "0.0.0.0"
    listen_port: int = 8080
    on_fail: str = "rewrite_402"  # rewrite_402 | pass_through
    validation_timeout: float = 10.0
    header_status: str = "X-Validation-Status"
    header_report: str = "X-Validation-Report"

    @classmethod
    def from_yaml(cls, path: str) -> ProxyConfig:
        if not os.path.exists(path):
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            listen_host=data.get("listen_host", cls.listen_host),
            listen_port=int(data.get("listen_port", cls.listen_port)),
            on_fail=data.get("on_fail", cls.on_fail),
            validation_timeout=float(
                data.get("validation", {}).get("timeout", cls.validation_timeout)
            ),
            header_status=data.get("headers", {}).get(
                "x_validation_status", cls.header_status
            ),
            header_report=data.get("headers", {}).get(
                "x_validation_report", cls.header_report
            ),
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def _validate_upstream(url: str, timeout: float) -> dict:
    """Run the x402 audit on ``url`` and return a JSON-friendly dict."""
    try:
        from x402_conformance_suite._engine import X402Auditor
        async with X402Auditor(timeout=timeout) as auditor:
            report = await auditor.run_full_audit(url)
        return {
            "url": report.target_url,
            "overall_status": report.overall_status,
            "summary": report.summary,
            "checks": [
                {
                    "name": c.check_name,
                    "status": c.status,
                    "message": c.message,
                }
                for c in report.checks
            ],
            "timestamp": report.timestamp.isoformat(),
        }
    except Exception as e:
        return {
            "url": url,
            "overall_status": "ERROR",
            "summary": f"Validator crashed: {e}",
            "checks": [],
            "timestamp": None,
        }


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


def _extract_target(request: web.Request) -> str | None:
    """Pull the target URL out of ``/forward/<host>/<path>``."""
    path = request.match_info.get("path", "")
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    return f"https://{path}"


def _filtered_request_headers(request: web.Request) -> dict[str, str]:
    """Strip hop-by-hop headers before forwarding."""
    SKIP = ("host", "x-forwarded-for", "x-forwarded-proto")
    return {
        k: v for k, v in request.headers.items() if k.lower() not in SKIP
    }


def _filtered_response_headers(upstream_headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop + content-length before returning to the client."""
    SKIP = ("transfer-encoding", "content-encoding", "content-length")
    return {k: v for k, v in upstream_headers.items() if k.lower() not in SKIP}


async def _fetch_upstream(
    method: str,
    url: str,
    headers: dict[str, str],
) -> tuple[int, bytes, dict[str, str]]:
    """Forward the request to the upstream and return ``(status, body, headers)``."""
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.request(
        method=method,
        url=url,
        headers=headers,
    ) as resp:
        body = await resp.read()
        return resp.status, body, dict(resp.headers)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _proxy_handler(request: web.Request) -> web.Response:
    target = _extract_target(request)
    if not target:
        return web.json_response(
            {"error": "Missing target URL — expected /forward/<host>/<path>"},
            status=400,
        )

    config: ProxyConfig = request.app[CONFIG_KEY]
    log.info("Proxying %s %s", request.method, target)

    try:
        upstream_status, upstream_body, upstream_headers = await _fetch_upstream(
            request.method,
            target,
            _filtered_request_headers(request),
        )
    except aiohttp.ClientError as e:
        log.error("Upstream error for %s: %s", target, e)
        return web.json_response(
            {"error": "Bad Gateway", "detail": str(e)},
            status=502,
        )

    validation = await _validate_upstream(target, config.validation_timeout)

    # Decide response status based on validation
    if validation["overall_status"] in ("FAIL", "CRITICAL_FAIL"):
        status_value = "FAIL"
        proxy_status = 402 if config.on_fail == "rewrite_402" else upstream_status
        body_override = config.on_fail == "rewrite_402"
    elif validation["overall_status"] == "ERROR":
        status_value = "ERROR"
        proxy_status = upstream_status
        body_override = False
    else:  # PASS
        status_value = "PASS"
        proxy_status = upstream_status
        body_override = False

    resp_headers = _filtered_response_headers(upstream_headers)
    resp_headers[config.header_status] = status_value
    resp_headers[config.header_report] = json.dumps(validation, ensure_ascii=False)

    if body_override and proxy_status == 402:
        try:
            parsed = json.loads(upstream_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = {"raw": upstream_body.decode("utf-8", errors="replace")}
        envelope = {
            "status": "validation_failed",
            "validation": validation,
            "upstream_response": parsed if isinstance(parsed, dict) else {"payload": parsed},
        }
        # Strip Content-Type before letting json_response add it
        resp_headers.pop("Content-Type", None)
        resp_headers.pop("content-type", None)
        return web.json_response(envelope, status=402, headers=resp_headers)

    return web.Response(
        status=proxy_status,
        body=upstream_body,
        headers=resp_headers,
    )


async def _root_handler(request: web.Request) -> web.Response:
    return web.json_response({"service": "x402-proxy", "status": "ok"})


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# App factory + entry point
# ---------------------------------------------------------------------------

# Typed app-key so aiohttp does not warn about bare string keys (NotAppKeyWarning).
CONFIG_KEY = web.AppKey("config", ProxyConfig)


def build_app(config: ProxyConfig | None = None) -> web.Application:
    """Build the aiohttp app; ``config`` defaults to whatever YAML at default path says."""
    if config is None:
        config = ProxyConfig.from_yaml("proxy/config.yaml")
    app = web.Application()
    app[CONFIG_KEY] = config
    app.router.add_route("*", "/forward/{path:.*}", _proxy_handler)
    app.router.add_route("*", "/health", _health_handler)
    app.router.add_route("*", "/", _root_handler)
    return app


def main() -> None:
    """Run with ``x402-proxy``."""
    config = ProxyConfig.from_yaml("proxy/config.yaml")
    log.info("Starting x402 proxy on %s:%d (on_fail=%s)",
             config.listen_host, config.listen_port, config.on_fail)
    web.run_app(build_app(config), host=config.listen_host, port=config.listen_port)


if __name__ == "__main__":
    main()
