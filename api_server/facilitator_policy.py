"""Facilitator reuse compensation policy (Fase 4a).

Pure functions only — no I/O, RPC, DB, or network.
Not wired to ``/validate`` or settlement yet.

Defaults and acceptance criteria: ``docs/facilitator_reuse_compensation.md``.
"""

from __future__ import annotations

from typing import Any, TypedDict

# Defaults (Modelo D hybrid) — recalibrate with real metrics exports.
DEFAULT_T = 3
DEFAULT_N_SOFT = 5  # T + 2
DEFAULT_M = 1.5
DEFAULT_N_MAX = 10


class ReuseSurchargeResult(TypedDict):
    state: str
    method: str
    surcharge_usdc: float
    alert: bool
    alert_method: str | None


def evaluate_reuse_surcharge(
    count_1h: int,
    gas_est_usd: float,
    gas_p50_usd: float,
    *,
    T: int = DEFAULT_T,
    N_soft: int = DEFAULT_N_SOFT,
    m: float = DEFAULT_M,
    N_max: int = DEFAULT_N_MAX,
) -> ReuseSurchargeResult:
    """Map 1h reuse count + gas estimates to a USDC surcharge policy.

    ``count_1h`` is 1-based inclusive of the call under evaluation (n).

    Fee tiers (``state`` stays ``allowed`` until hard-block exists):

    - n < T              → first_free, 0
    - T <= n <= N_soft   → surcharge_applied, m * gas_est_usd
    - n > N_soft         → surcharge_escalated, m * gas_p50_usd * (n - T + 1)

    If n > N_max, set ``alert=True`` / ``alert_method="reuse_cap_1h"``
    without changing the surcharge (alert only, not a block).
    """
    if count_1h < 0:
        raise ValueError("count_1h must be >= 0")
    if T < 1:
        raise ValueError("T must be >= 1")
    if N_soft < T:
        raise ValueError("N_soft must be >= T")
    if N_max < N_soft:
        raise ValueError("N_max must be >= N_soft")
    if m < 0:
        raise ValueError("m must be >= 0")
    if gas_est_usd < 0 or gas_p50_usd < 0:
        raise ValueError("gas estimates must be >= 0")

    n = count_1h

    if n < T:
        state = "allowed"
        method = "first_free"
        surcharge = 0.0
    elif n <= N_soft:
        state = "allowed"
        method = "surcharge_applied"
        surcharge = float(m) * float(gas_est_usd)
    else:
        state = "allowed"
        method = "surcharge_escalated"
        k = n - T + 1
        surcharge = float(m) * float(gas_p50_usd) * float(k)

    alert = n > N_max
    alert_method = "reuse_cap_1h" if alert else None

    return {
        "state": state,
        "method": method,
        "surcharge_usdc": surcharge,
        "alert": alert,
        "alert_method": alert_method,
    }


def evaluate_reuse_surcharge_deferred() -> ReuseSurchargeResult:
    """Pre-settle: wallet not known yet — no fee decision."""
    return {
        "state": "deferred",
        "method": "await_wallet",
        "surcharge_usdc": 0.0,
        "alert": False,
        "alert_method": None,
    }


# Back-compat alias for typed callers that want a plain dict annotation.
def result_as_dict(result: ReuseSurchargeResult) -> dict[str, Any]:
    return dict(result)
