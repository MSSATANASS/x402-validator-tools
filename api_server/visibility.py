"""Directory visibility probes for x402 endpoints.

Bazaar-style agent directories discover paid endpoints with a *cold probe*:
a bare POST — no body, no auth, no special headers. A conformant x402
endpoint answers that probe with ``402 Payment Required`` so the directory
can read the challenge and index the resource. Any other answer means the
directory never indexes the endpoint (or silently de-indexes it) while real,
paying buyers keep working — revenue looks fine, discovery is broken.

``check_directory_cold_probe`` implements that probe as a CheckResult-shaped
dict, mirroring the engine's never-raise contract (status in
``{"PASS", "FAIL", "ERROR"}`` with an operator-actionable message).
"""

from __future__ import annotations

from typing import Any

import httpx

CHECK_NAME = "directory_cold_probe"


def _result(status: str, message: str, status_code: int | None) -> dict[str, Any]:
    """Build the CheckResult-shaped dict every probe outcome returns."""
    return {
        "check_name": CHECK_NAME,
        "status": status,
        "message": message,
        "details": {"method": "POST", "status_code": status_code},
    }


async def check_directory_cold_probe(
    url: str,
    timeout: float = 10.0,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """POST to ``url`` with no body and no auth; PASS only on a 402 challenge.

    This replays the discovery probe directories (Bazaar/CDP) run against a
    merchant endpoint. Returns a CheckResult-shaped dict and never raises:
    network/timeout/unexpected failures come back as ``status="ERROR"``.

    Args:
        url: Target endpoint URL (the audited merchant URL).
        timeout: HTTP request timeout in seconds.
        transport: Optional httpx transport (testing with MockTransport).
    """
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, transport=transport
        ) as client:
            response = await client.post(url)
    except httpx.TimeoutException:
        return _result(
            "ERROR",
            f"Directory cold probe timed out after {timeout}s. "
            f"Verify the endpoint is reachable.",
            None,
        )
    except Exception as e:  # noqa: BLE001 — checks never raise
        return _result(
            "ERROR",
            f"Could not run the directory cold probe: {e}. "
            f"Verify the URL is live and reachable.",
            None,
        )

    code = response.status_code
    if code == 402:
        return _result(
            "PASS",
            "Directory cold probe sees your 402 challenge — the endpoint is "
            "indexable by Bazaar-style directories.",
            code,
        )
    if code in (401, 403):
        return _result(
            "FAIL",
            f"The directory's bare POST got HTTP {code} — an auth gate "
            f"answers before the payment gate. Directories probe "
            f"unauthenticated; let bare requests reach your 402 challenge.",
            code,
        )
    if code == 405:
        return _result(
            "FAIL",
            "POST is not allowed on this resource (HTTP 405). If the "
            "resource is GET-only, directories' POST probe will never see "
            "your 402 challenge.",
            code,
        )
    if code == 400 or code >= 500:
        return _result(
            "FAIL",
            f"The directory's bare POST (no body, no auth) got HTTP {code} "
            f"— body validation runs before the payment gate. Buyers can "
            f"still pay, but directories never see your 402 and silently "
            f"de-index you. Fix: run body validation after the payment gate.",
            code,
        )
    if 200 <= code < 300:
        return _result(
            "FAIL",
            f"POST returned HTTP {code} without a 402 challenge — there is "
            f"no payment gate for this method.",
            code,
        )
    return _result(
        "FAIL",
        f"The directory's bare POST got HTTP {code}; directories expect 402 "
        f"on a cold probe. Check what answers on this route before the "
        f"payment gate.",
        code,
    )
