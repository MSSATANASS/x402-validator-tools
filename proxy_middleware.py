import asyncio
import json
import logging
import os
from typing import Any

import aiohttp
from aiohttp import web
import yaml

from x402_conformance_engine import X402Auditor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)
log = logging.getLogger("x402-proxy")


DEFAULT_CONFIG = {
    "listen_host": "0.0.0.0",
    "listen_port": 8080,
    "upstreams": {},
    "validation": {"strict_mode": False, "timeout": 10.0},
    "headers": {
        "x-validation-status": "X-Validation-Status",
        "x-validation-report": "X-Validation-Report",
    },
}


def load_config(path: str = "proxy_config.yaml") -> dict[str, Any]:
    if not os.path.exists(path):
        return dict(DEFAULT_CONFIG)
    with open(path, "r") as f:
        return {**DEFAULT_CONFIG, **yaml.safe_load(f)}


def extract_target(request: web.Request) -> str | None:
    path = request.match_info.get("path", "")
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"https://{path}"


async def validate_upstream(url: str, timeout: float) -> dict[str, Any]:
    try:
        async with X402Auditor(timeout=timeout) as auditor:
            report = await auditor.run_full_audit(url)
        return {
            "overall_status": report.overall_status,
            "summary": report.summary,
            "checks": {c.check_name: {"status": c.status, "message": c.message} for c in report.checks},
        }
    except Exception as e:
        return {
            "overall_status": "ERROR",
            "summary": f"Validation error: {e}",
            "checks": {},
        }


async def fetch_upstream(
    method: str,
    url: str,
    headers: dict[str, str] | None,
) -> tuple[int, bytes, dict[str, str]]:
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method=method,
            url=url,
            headers=headers or None,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            body = await resp.read()
            return resp.status, body, dict(resp.headers)


async def proxy_handler(request: web.Request) -> web.Response:
    target = extract_target(request)
    if not target:
        return web.json_response({"error": "Missing target URL in path"}, status=400)

    log.info("Proxying %s %s", request.method, target)

    headers_to_forward: dict[str, str] = {}
    for k, v in request.headers.items():
        lower = k.lower()
        if lower in ("host", "x-forwarded-for", "x-forwarded-proto"):
            continue
        headers_to_forward[k] = v

    try:
        upstream_status, upstream_body, upstream_headers = await fetch_upstream(
            request.method, target, headers_to_forward,
        )
    except aiohttp.ClientError as e:
        log.error("Upstream error: %s", e)
        return web.json_response({"error": "Bad Gateway", "detail": str(e)}, status=502)

    config = load_config()
    validation_result = await validate_upstream(target, config["validation"]["timeout"])

    status_header = config["headers"]["x-validation-status"]
    report_header = config["headers"]["x-validation-report"]

    if validation_result["overall_status"] == "PASS":
        status_value = "PASS"
        proxy_status = upstream_status
    elif validation_result["overall_status"] in ("FAIL", "CRITICAL_FAIL"):
        status_value = "FAIL"
        proxy_status = 402
    else:
        status_value = "WARN"
        proxy_status = upstream_status

    resp_headers: dict[str, str] = {}
    for k, v in upstream_headers.items():
        lower = k.lower()
        if lower in ("transfer-encoding", "content-encoding", "content-length"):
            continue
        resp_headers[k] = v

    resp_headers[status_header] = status_value
    resp_headers[report_header] = json.dumps(validation_result, ensure_ascii=False)

    if proxy_status == 402:
        try:
            parsed = json.loads(upstream_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = {"raw": upstream_body.decode("utf-8", errors="replace")}

        error_body = {
            "status": "validation_failed",
            "validation": validation_result,
            "upstream_response": parsed if isinstance(parsed, dict) else {"payload": parsed},
        }
        body = json.dumps(error_body, ensure_ascii=False).encode("utf-8")
        resp_headers["content-type"] = "application/json; charset=utf-8"
        return web.Response(status=402, body=body, headers=resp_headers)

    return web.Response(
        status=proxy_status,
        body=upstream_body,
        headers=resp_headers,
    )


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_route("*", "/forward/{path:.*}", proxy_handler)
    app.router.add_route("*", "/", lambda r: web.json_response({"service": "x402-proxy", "status": "ok"}))
    return app


def main() -> None:
    config = load_config()
    host = config["listen_host"]
    port = config["listen_port"]
    log.info("Starting x402 proxy on %s:%d", host, port)
    app = build_app()
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
