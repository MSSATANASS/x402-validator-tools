"""Pure evaluator for x402 batch-settlement EVM PaymentRequirements.

Never performs HTTP. Callers supply a decoded PaymentRequired payload
(or None) plus the observed HTTP status.
"""
from __future__ import annotations

import re
from typing import Any

CHECK_NAME = "batch_settlement_requirements"
FINDINGS_CAP = 20
SCHEME = "batch-settlement"

SPEC_REF_COMMIT = "266b19d2251356ee958a1f4ffaa4e57aa2007f33"

SPEC_REF: dict[str, Any] = {
    "scheme": "batch-settlement",
    "binding": "evm",
    "doc": (
        "https://github.com/x402-foundation/x402/blob/"
        f"{SPEC_REF_COMMIT}/"
        "specs/schemes/batch-settlement/scheme_batch_settlement_evm.md"
    ),
    "commit": SPEC_REF_COMMIT,
    "required_extra_fields": [
        "receiverAuthorizer",
        "withdrawDelay",
        "name",
        "version",
    ],
}

_NETWORK_RE = re.compile(r"^eip155:([1-9][0-9]*)$")
_AMOUNT_RE = re.compile(r"^[1-9][0-9]*$")
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ZERO_ADDRESS = "0x" + ("0" * 40)

_WITHDRAW_DELAY_MIN = 900
_WITHDRAW_DELAY_MAX = 2_592_000

_ASSET_TRANSFER_METHODS = frozenset({"eip3009", "permit2"})


def _finding(
    accepts_index: int,
    field: str,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "accepts_index": accepts_index,
        "field": field,
        "code": code,
        "message": message,
    }


def _is_valid_address(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_ADDRESS_RE.fullmatch(value))


def _is_zero_address(value: str) -> bool:
    return value.lower() == _ZERO_ADDRESS


def _check_address(
    value: Any,
    *,
    accepts_index: int,
    field: str,
    code_prefix: str,
) -> list[dict[str, Any]]:
    label = f"accepts[{accepts_index}]: {field}"
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return [
            _finding(
                accepts_index,
                field,
                f"missing_{code_prefix}",
                f"{label} is required for batch-settlement (EVM)",
            )
        ]
    if not isinstance(value, str) or not _is_valid_address(value):
        return [
            _finding(
                accepts_index,
                field,
                f"invalid_{code_prefix}",
                f"{label} must be 0x + 40 hex characters (EVM address)",
            )
        ]
    if _is_zero_address(value):
        return [
            _finding(
                accepts_index,
                field,
                f"zero_{code_prefix}",
                f"{label} must not be the zero address",
            )
        ]
    return []


def _parse_withdraw_delay(value: Any) -> int | None:
    """Return int delay if valid digit form, else None (caller emits finding)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s or not re.fullmatch(r"[1-9][0-9]*|0", s):
            # allow "0" to parse so range check can reject it; reject leading zeros
            if re.fullmatch(r"0[0-9]+", s):
                return None
            if re.fullmatch(r"[0-9]+", s):
                return int(s)
            return None
        # no leading zeros except plain 0
        if len(s) > 1 and s.startswith("0"):
            return None
        return int(s)
    return None


def _validate_entry(
    entry: Any,
    accepts_index: int,
    aliases_used: list[str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    prefix = f"accepts[{accepts_index}]"

    if not isinstance(entry, dict):
        findings.append(
            _finding(
                accepts_index,
                "entry",
                "invalid_entry_type",
                f"{prefix}: batch-settlement entry must be an object",
            )
        )
        return findings

    # network
    network = entry.get("network")
    if not isinstance(network, str) or not _NETWORK_RE.fullmatch(network.strip()):
        findings.append(
            _finding(
                accepts_index,
                "network",
                "invalid_network",
                f"{prefix}: network must match eip155:<positive chainId without leading zeros>",
            )
        )

    # amount
    amount = entry.get("amount")
    if not isinstance(amount, str) or not _AMOUNT_RE.fullmatch(amount):
        findings.append(
            _finding(
                accepts_index,
                "amount",
                "invalid_amount",
                f"{prefix}: amount must be a digit string with no leading zeros and value >= 1",
            )
        )

    # asset
    findings.extend(
        _check_address(
            entry.get("asset"),
            accepts_index=accepts_index,
            field="asset",
            code_prefix="asset",
        )
    )

    # payTo (canonical) / pay_to (alias only if payTo missing)
    has_pay_to_camel = "payTo" in entry and entry.get("payTo") is not None
    has_pay_to_snake = "pay_to" in entry and entry.get("pay_to") is not None

    if has_pay_to_camel and has_pay_to_snake:
        a = entry.get("payTo")
        b = entry.get("pay_to")
        if a != b:
            findings.append(
                _finding(
                    accepts_index,
                    "payTo",
                    "payto_alias_conflict",
                    f"{prefix}: payTo and pay_to both present and differ",
                )
            )
        # still validate the canonical value when present
        findings.extend(
            _check_address(
                a,
                accepts_index=accepts_index,
                field="payTo",
                code_prefix="pay_to",
            )
        )
    elif has_pay_to_camel:
        findings.extend(
            _check_address(
                entry.get("payTo"),
                accepts_index=accepts_index,
                field="payTo",
                code_prefix="pay_to",
            )
        )
    elif has_pay_to_snake:
        if "pay_to" not in aliases_used:
            aliases_used.append("pay_to")
        findings.extend(
            _check_address(
                entry.get("pay_to"),
                accepts_index=accepts_index,
                field="pay_to",
                code_prefix="pay_to",
            )
        )
    else:
        findings.append(
            _finding(
                accepts_index,
                "payTo",
                "missing_pay_to",
                f"{prefix}: payTo is required for batch-settlement (EVM)",
            )
        )

    # extra
    extra = entry.get("extra")
    if not isinstance(extra, dict):
        findings.append(
            _finding(
                accepts_index,
                "extra",
                "invalid_extra",
                f"{prefix}: extra must be an object for batch-settlement (EVM)",
            )
        )
        return findings

    # receiverAuthorizer — camelCase only (no snake_case alias)
    findings.extend(
        _check_address(
            extra.get("receiverAuthorizer"),
            accepts_index=accepts_index,
            field="extra.receiverAuthorizer",
            code_prefix="receiver_authorizer",
        )
    )

    # withdrawDelay
    if "withdrawDelay" not in extra:
        findings.append(
            _finding(
                accepts_index,
                "extra.withdrawDelay",
                "missing_withdraw_delay",
                f"{prefix}: extra.withdrawDelay is required for batch-settlement (EVM)",
            )
        )
    else:
        delay = _parse_withdraw_delay(extra.get("withdrawDelay"))
        if delay is None:
            findings.append(
                _finding(
                    accepts_index,
                    "extra.withdrawDelay",
                    "invalid_withdraw_delay",
                    f"{prefix}: extra.withdrawDelay must be an int or digit string without leading zeros",
                )
            )
        elif delay < _WITHDRAW_DELAY_MIN or delay > _WITHDRAW_DELAY_MAX:
            findings.append(
                _finding(
                    accepts_index,
                    "extra.withdrawDelay",
                    "withdraw_delay_out_of_range",
                    f"{prefix}: extra.withdrawDelay must be in [{_WITHDRAW_DELAY_MIN}, {_WITHDRAW_DELAY_MAX}]",
                )
            )

    # name
    name = extra.get("name")
    if not isinstance(name, str) or not name.strip():
        findings.append(
            _finding(
                accepts_index,
                "extra.name",
                "missing_or_empty_name",
                f"{prefix}: extra.name is required (non-empty string) for EIP-712 domain",
            )
        )

    # version
    version = extra.get("version")
    if not isinstance(version, str) or not version.strip():
        findings.append(
            _finding(
                accepts_index,
                "extra.version",
                "missing_or_empty_version",
                f"{prefix}: extra.version is required (non-empty string) for EIP-712 domain",
            )
        )

    # assetTransferMethod optional
    if "assetTransferMethod" in extra:
        atm = extra.get("assetTransferMethod")
        if atm not in _ASSET_TRANSFER_METHODS:
            findings.append(
                _finding(
                    accepts_index,
                    "extra.assetTransferMethod",
                    "invalid_asset_transfer_method",
                    f"{prefix}: extra.assetTransferMethod must be 'eip3009' or 'permit2' if present",
                )
            )

    return findings


def _is_batch_scheme(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    scheme = entry.get("scheme")
    if not isinstance(scheme, str):
        return False
    return scheme.strip() == SCHEME


def evaluate_batch_settlement_requirements(
    payload: dict[str, Any] | None,
    *,
    http_status: int | None,
    target_url: str,
    payload_source: str = "none",
) -> dict[str, Any]:
    """Evaluate batch-settlement EVM requirements on a decoded PaymentRequired.

    Pure function: no I/O. Returns a CheckResult-shaped dict.
    """
    base_details: dict[str, Any] = {
        "status_code": http_status,
        "applicable": False,
        "batch_entries": 0,
        "findings": [],
        "findings_total": 0,
        "aliases_used": [],
        "payload_source": payload_source,
        "spec_ref": dict(SPEC_REF),
        "target_url": target_url,
    }

    # Non-402 → N/A PASS
    if http_status != 402:
        return {
            "check_name": CHECK_NAME,
            "status": "PASS",
            "message": "N/A — response is not HTTP 402 Payment Required",
            "details": {**base_details, "applicable": False},
        }

    # 402 but no payload → ERROR
    if payload is None:
        return {
            "check_name": CHECK_NAME,
            "status": "ERROR",
            "message": "PaymentRequired payload is missing or could not be decoded",
            "details": {**base_details, "applicable": None},
        }

    if not isinstance(payload, dict):
        return {
            "check_name": CHECK_NAME,
            "status": "ERROR",
            "message": "PaymentRequired payload is not a JSON object",
            "details": {**base_details, "applicable": None},
        }

    accepts = payload.get("accepts")
    if accepts is None:
        accepts = []
    if not isinstance(accepts, list):
        return {
            "check_name": CHECK_NAME,
            "status": "ERROR",
            "message": "PaymentRequired.accepts is not an array",
            "details": {**base_details, "applicable": None},
        }

    batch_indices: list[int] = []
    all_findings: list[dict[str, Any]] = []
    aliases_used: list[str] = []

    for idx, entry in enumerate(accepts):
        if not _is_batch_scheme(entry):
            continue
        batch_indices.append(idx)
        all_findings.extend(_validate_entry(entry, idx, aliases_used))

    batch_entries = len(batch_indices)
    findings_total = len(all_findings)
    capped = all_findings[:FINDINGS_CAP]

    details = {
        **base_details,
        "batch_entries": batch_entries,
        "findings": capped,
        "findings_total": findings_total,
        "aliases_used": list(aliases_used),
    }

    if batch_entries == 0:
        details["applicable"] = False
        return {
            "check_name": CHECK_NAME,
            "status": "PASS",
            "message": "N/A — no batch-settlement entries in accepts[]",
            "details": details,
        }

    details["applicable"] = True
    if findings_total == 0:
        return {
            "check_name": CHECK_NAME,
            "status": "PASS",
            "message": f"{batch_entries} batch-settlement offer(s) conform to EVM requirements",
            "details": details,
        }

    first_msg = all_findings[0]["message"]
    return {
        "check_name": CHECK_NAME,
        "status": "FAIL",
        "message": f"{findings_total} finding(s); first: {first_msg}",
        "details": details,
    }
