# Batch settlement requirements check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add check `batch_settlement_requirements` that validates EVM `PaymentRequirements` for `scheme: "batch-settlement"` without an extra GET on the cold-probe happy path, and surface it as the 9th check on `/validate`, `/audit-public`, and the landing.

**Architecture:** Pure evaluate function (no HTTP) + small PaymentRequired decoder + cold-probe response snapshot for payload reuse + one GET fallback. Wire through `_run_audit` and `_aggregate_check_results` (already recomputes overall/summary after tools-side appends).

**Tech Stack:** Python 3.11+, httpx, FastAPI, pytest, existing `api_server` patterns (`visibility.py`, `_aggregate_check_results`).

**Spec (approved):** `docs/superpowers/specs/2026-08-08-batch-settlement-requirements-design.md`

## Global Constraints

- Status values only: `PASS` | `FAIL` | `ERROR` (no `WARN`; no `CRITICAL_FAIL` in this check).
- `applicable`: tri-state `true` | `false` | `null` (null ⇔ ERROR / indeterminate).
- `spec_ref.commit` must be the full 40-hex SHA: `266b19d2251356ee958a1f4ffaa4e57aa2007f33`.
- Header lookup: lowercase keys; precedence body > `payment-required` > `x-payment-required`.
- `FINDINGS_CAP = 20`; always set `findings_total` to the uncapped count.
- `payTo` canonical; `pay_to` only alias; no `receiver_authorizer` alias.
- Zero GET tools-side when cold probe yields decodable 402; otherwise one GET fallback.
- Landing copy: eight → nine checks (FAQ, JSON-LD, demo blurb).
- Identity for commits: per-invocation `-c user.name=...` as in prior work; no `git config` mutation.
- Do not track session export `.txt` files.

## File map

| Path | Role |
|------|------|
| `api_server/payment_required.py` | Decode PaymentRequired from body/headers (precedence + case-insensitivity). |
| `api_server/batch_settlement.py` | Pure `evaluate_batch_settlement_requirements` + constants (`FINDINGS_CAP`, `SPEC_REF`). |
| `api_server/visibility.py` | Return `(check_result, response_snapshot \| None)` for orchestrator reuse; keep public check shape. |
| `api_server/app.py` | Orchestrate payload source; append check; landing nine-check copy. |
| `tests/test_payment_required.py` | Decoder unit tests. |
| `tests/test_batch_settlement.py` | Evaluate unit tests + orchestration (no extra GET). |
| `tests/test_api_server.py` | Landing “nine”, `/validate`/`/audit-public` includes new check; autouse probe snapshot if needed. |
| `tests/test_visibility.py` | Adjust if cold-probe return shape changes. |

---

### Task 1: PaymentRequired decoder

**Files:**
- Create: `api_server/payment_required.py`
- Create: `tests/test_payment_required.py`

**Interfaces:**
- Produces:
  - `decode_payment_required(*, body: str | bytes | None, headers: Mapping[str, str] | None) -> dict | None`
  - `decode_from_httpx_response(response: httpx.Response) -> dict | None` (thin wrapper)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_payment_required.py
import base64
import json
from api_server.payment_required import decode_payment_required


def _b64(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def test_body_wins_over_payment_required_header():
    body = {"x402Version": 2, "accepts": [{"scheme": "exact", "from": "body"}]}
    hdr = {"from": "header"}
    out = decode_payment_required(
        body=json.dumps(body),
        headers={"payment-required": _b64(hdr)},
    )
    assert out["accepts"][0]["from"] == "body"


def test_payment_required_wins_over_x_payment_required():
    a = {"src": "payment-required"}
    b = {"src": "x-payment-required"}
    out = decode_payment_required(
        body=None,
        headers={
            "payment-required": _b64(a),
            "x-payment-required": _b64(b),
        },
    )
    assert out["src"] == "payment-required"


def test_header_lookup_is_case_insensitive():
    payload = {"src": "mixed"}
    out = decode_payment_required(
        body="",
        headers={"Payment-Required": _b64(payload)},
    )
    assert out["src"] == "mixed"


def test_malformed_returns_none():
    assert decode_payment_required(body="not-json", headers=None) is None
    assert decode_payment_required(body=None, headers={"payment-required": "!!!"}) is None
```

- [ ] **Step 2: Run tests — expect FAIL (module missing)**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_payment_required.py -q
```

- [ ] **Step 3: Implement `api_server/payment_required.py`**

```python
"""Decode x402 PaymentRequired from HTTP body and/or headers.

Precedence (first successful object wins; sources are not merged):
  1. Body JSON object
  2. Header ``payment-required`` (base64 JSON)
  3. Header ``x-payment-required`` (base64 JSON)

Header names are matched case-insensitively (HTTP standard): keys are
lowercased before lookup.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Mapping


def _lower_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _b64_to_obj(token: str) -> dict[str, Any] | None:
    try:
        padded = token + ("=" * (-len(token) % 4))
        try:
            raw = base64.b64decode(padded, validate=False)
        except Exception:
            raw = base64.urlsafe_b64decode(padded)
        obj = json.loads(raw.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _body_to_obj(body: str | bytes | None) -> dict[str, Any] | None:
    if body is None:
        return None
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except Exception:
            return None
    text = body.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def decode_payment_required(
    *,
    body: str | bytes | None,
    headers: Mapping[str, str] | None,
) -> dict[str, Any] | None:
    """Return decoded PaymentRequired object or None if none of the sources work."""
    obj = _body_to_obj(body)
    if obj is not None:
        return obj
    h = _lower_headers(headers)
    for key in ("payment-required", "x-payment-required"):
        raw = h.get(key)
        if not raw:
            continue
        obj = _b64_to_obj(raw)
        if obj is not None:
            return obj
    return None


def decode_from_httpx_response(response) -> dict[str, Any] | None:
    """Convenience wrapper for httpx.Response."""
    return decode_payment_required(body=response.text, headers=response.headers)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_payment_required.py -q
```

- [ ] **Step 5: Commit**

```bash
git add api_server/payment_required.py tests/test_payment_required.py
git commit -m "feat: PaymentRequired decoder with body/header precedence"
```

---

### Task 2: Pure batch-settlement evaluator

**Files:**
- Create: `api_server/batch_settlement.py`
- Create: `tests/test_batch_settlement.py`

**Interfaces:**
- Produces:
  - `CHECK_NAME = "batch_settlement_requirements"`
  - `FINDINGS_CAP = 20`
  - `SPEC_REF: dict` with `commit` length 40
  - `evaluate_batch_settlement_requirements(payload, *, http_status, target_url, payload_source="none") -> dict`

- [ ] **Step 1: Write failing tests (core matrix from spec)**

Implement at least these cases in `tests/test_batch_settlement.py` (all call pure evaluate only):

1. exact-only 402 → PASS, `applicable is False`
2. full valid batch-settlement entry → PASS, `applicable is True`
3. missing `receiverAuthorizer` → FAIL + `accepts_index`
4. `withdrawDelay` 60 → FAIL
5. `eip155:08453` → FAIL; non-EVM network → FAIL
6. amount `"0"`, `"0.01"`, `"007"`, `"-1"` → FAIL
7. zero address on asset/payTo/receiverAuthorizer → FAIL
8. address length 39/41 → FAIL
9. `extra` null / list / string → FAIL
10. multi-entry one good one bad → FAIL, `batch_entries==2`, correct index
11. `assetTransferMethod` absent → PASS; garbage → FAIL
12. only `pay_to` → PASS + `aliases_used`; both differ → FAIL
13. `http_status != 402` → PASS, `applicable is False`
14. payload None + status 402 → ERROR, `applicable is None`
15. 25 broken entries → `len(findings)==20`, `findings_total==25`
16. `receiver_authorizer` snake_case only → FAIL (no alias)
17. `SPEC_REF["commit"]` len 40 and hex-only

Helper for a **valid** entry:

```python
VALID = {
    "scheme": "batch-settlement",
    "network": "eip155:8453",
    "amount": "100000",
    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "payTo": "0x1111111111111111111111111111111111111111",
    "maxTimeoutSeconds": 3600,
    "extra": {
        "receiverAuthorizer": "0x2222222222222222222222222222222222222222",
        "withdrawDelay": 900,
        "name": "USDC",
        "version": "2",
    },
}
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_batch_settlement.py -q
```

- [ ] **Step 3: Implement `api_server/batch_settlement.py`**

Rules (copy from approved spec — implement exactly):

- Scheme string exact: `"batch-settlement"` (strip).
- `network`: regex `^eip155:([1-9][0-9]*)$`
- `amount`: digit string, no leading zeros, int value ≥ 1
- Addresses: `^0x[0-9a-fA-F]{40}$`, reject zero address; no EIP-55 check
- `payTo` preferred; `pay_to` alias only if `payTo` missing; both present and differ → finding
- `extra` must be dict
- Required extra: `receiverAuthorizer` (address), `withdrawDelay` in [900, 2592000], `name` non-empty str, `version` non-empty str
- Optional `assetTransferMethod` ∈ {`eip3009`, `permit2`} if present
- Cap findings at 20; set `findings_total`
- Top-level message uses `findings_total`
- `details` includes `status_code`, `applicable`, `batch_entries`, `findings`, `findings_total`, `aliases_used`, `payload_source`, `spec_ref`

```python
SPEC_REF = {
    "scheme": "batch-settlement",
    "binding": "evm",
    "doc": (
        "https://github.com/x402-foundation/x402/blob/"
        "266b19d2251356ee958a1f4ffaa4e57aa2007f33/"
        "specs/schemes/batch-settlement/scheme_batch_settlement_evm.md"
    ),
    "commit": "266b19d2251356ee958a1f4ffaa4e57aa2007f33",
    "required_extra_fields": [
        "receiverAuthorizer",
        "withdrawDelay",
        "name",
        "version",
    ],
}
FINDINGS_CAP = 20
CHECK_NAME = "batch_settlement_requirements"
```

Result shape:

```python
{
  "check_name": CHECK_NAME,
  "status": "PASS" | "FAIL" | "ERROR",
  "message": str,
  "details": { ... },
}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_batch_settlement.py -q
```

- [ ] **Step 5: Commit**

```bash
git add api_server/batch_settlement.py tests/test_batch_settlement.py
git commit -m "feat: pure batch_settlement_requirements evaluator (EVM PaymentRequirements)"
```

---

### Task 3: Cold-probe response snapshot

**Files:**
- Modify: `api_server/visibility.py`
- Modify: `tests/test_visibility.py` (and autouse fixtures that mock the probe)

**Interfaces:**
- Change `check_directory_cold_probe` to return either:
  - **Preferred:** keep returning the CheckResult dict for external callers, **and** add:

```python
async def run_directory_cold_probe(...) -> tuple[dict, ResponseSnapshot | None]:
    """Returns (check_result, snapshot). snapshot is None on network ERROR."""
```

  - Or change internal API used only by `_run_audit` to the tuple form and leave a thin wrapper.

`ResponseSnapshot` minimal:

```python
@dataclass
class ResponseSnapshot:
    status_code: int
    headers: dict[str, str]  # original or lowercased — decoder lowercases again
    body: str
```

On successful HTTP response (any status), snapshot is filled. On timeout/exception, snapshot is `None`.

- [ ] **Step 1: Update/add visibility tests for snapshot presence on 402 and absence on transport error**

- [ ] **Step 2: Implement snapshot return path without changing public message semantics**

- [ ] **Step 3: Fix all mocks** of `check_directory_cold_probe` / `run_directory_cold_probe` in:
  - `tests/test_api_server.py` (`_no_real_cold_probe`)
  - `tests/test_ai_advisor.py` if it patches the same symbol

  Autouse fake should return a 402 snapshot with a simple exact-only payload when using the new tuple API, or still return dict if wrapper unchanged and `_run_audit` uses the new function name only.

**Recommendation:** introduce `run_directory_cold_probe` → tuple; keep `check_directory_cold_probe` as:

```python
async def check_directory_cold_probe(...):
    result, _ = await run_directory_cold_probe(...)
    return result
```

`_run_audit` will call `run_directory_cold_probe` only.

- [ ] **Step 4: Full visibility + api_server tests green**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_visibility.py tests/test_api_server.py -q
```

- [ ] **Step 5: Commit**

```bash
git add api_server/visibility.py tests/test_visibility.py tests/test_api_server.py tests/test_ai_advisor.py
git commit -m "feat: cold-probe response snapshot for PaymentRequired reuse"
```

---

### Task 4: Wire into `_run_audit` + aggregate

**Files:**
- Modify: `api_server/app.py` (`_run_audit`, `/validate`, `/audit-public`)
- Modify: `tests/test_batch_settlement.py` (integration) and/or `tests/test_api_server.py`

**Interfaces:**
- `_run_audit` returns `(report, cold_probe_result, batch_check_result)`
- Payload resolution:
  1. If snapshot status == 402 and `decode_from_httpx` / decode on snapshot body+headers works → `payload_source="cold_probe_post"`
  2. Else one GET with httpx (timeout same as audit); decode → `payload_source="fallback_get"` (or `"none"` on failure)
  3. `evaluate_batch_settlement_requirements(payload, http_status=..., target_url=url, payload_source=...)`
- Append **both** cold probe and batch check to `checks[]`
- Call existing `_aggregate_check_results(checks)` for overall/summary (already fixed in `58bff1d`)

- [ ] **Step 1: Failing integration tests**

```python
# In tests/test_batch_settlement.py or test_api_server.py

def test_audit_public_includes_batch_settlement_check(client, monkeypatch):
    # fake engine report + cold probe 402 with exact-only payload
    # assert last-or-named check batch_settlement_requirements
    # assert applicable false, status PASS
    # assert summary denominator counts engine + cold + batch


def test_no_fallback_get_when_cold_402(monkeypatch):
    # MockTransport or request counter: only POST from cold probe, zero tools GET
    # when cold returns 402 with body


def test_fallback_get_when_cold_not_402(monkeypatch):
    # cold 405; one GET returns 402 exact-only; payload_source fallback_get
```

- [ ] **Step 2: Implement orchestration in `app.py`**

Sketch:

```python
async def _run_audit(url: str, mode: str, timeout: float = 10.0):
    from x402_conformance_suite._engine import run_audit
    from api_server.visibility import run_directory_cold_probe
    from api_server.payment_required import decode_payment_required
    from api_server.batch_settlement import evaluate_batch_settlement_requirements

    report, (probe, snap) = await asyncio.gather(
        run_audit(url, timeout=timeout, mode=mode),
        run_directory_cold_probe(url, timeout),
    )

    payload = None
    http_status = None
    source = "none"
    if snap is not None and snap.status_code == 402:
        payload = decode_payment_required(body=snap.body, headers=snap.headers)
        http_status = 402
        if payload is not None:
            source = "cold_probe_post"

    if source == "none":
        # single GET fallback (never-raise: on error leave payload None)
        ...
        source = "fallback_get" if got_response else "none"
        http_status = ...

    batch = evaluate_batch_settlement_requirements(
        payload,
        http_status=http_status,
        target_url=url,
        payload_source=source,
    )
    return report, probe, batch
```

Update `/validate` and `/audit-public` to unpack three values and append both tools checks before `_aggregate_check_results`.

- [ ] **Step 3: Fix all call sites / patches of `_run_audit` return arity**

- [ ] **Step 4: Run suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests -q
```

- [ ] **Step 5: Commit**

```bash
git add api_server/app.py tests/
git commit -m "feat: wire batch_settlement_requirements into audit pipeline"
```

---

### Task 5: Landing + FAQ “nine checks”

**Files:**
- Modify: `api_server/app.py` (landing HTML, JSON-LD FAQ strings, demo subcopy)
- Modify: `api_server/pages.py` only if check count mentioned
- Modify: `tests/test_api_server.py` assertions that mention `eight` / `directory_cold_probe` list

- [ ] **Step 1: Grep for eight-check copy**

```bash
rg -n "eight checks|seven checks|directory_cold_probe" api_server tests
```

- [ ] **Step 2: Update every user-facing count to nine and add `batch_settlement_requirements` to enumerated lists (FAQ, JSON-LD)**

Example FAQ line:

```text
The same nine checks as /validate: manifest_discovery, caip2_compliance,
json_resilience, bazaar_compliance, bot_wall, accepts_completeness,
discovery_resource_listing, directory_cold_probe, batch_settlement_requirements.
```

- [ ] **Step 3: Update landing tests**

```python
assert "nine checks" in r.text.lower() or "nine checks" in r.text
assert "batch_settlement_requirements" in r.text
assert "seven checks" not in r.text
# keep eight only if intentionally historical — prefer remove
```

- [ ] **Step 4: pytest landing subset green**

- [ ] **Step 5: Commit**

```bash
git add api_server/app.py tests/test_api_server.py
git commit -m "docs(ui): nine checks including batch_settlement_requirements"
```

---

### Task 6: Final verification + push

- [ ] **Step 1: Full test suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests -q
```

Expected: all previous greens + new tests; no regressions.

- [ ] **Step 2: Manual sanity (optional local server)**

```bash
# with venv
.\.venv\Scripts\python.exe -c "from api_server.batch_settlement import SPEC_REF; assert len(SPEC_REF['commit'])==40"
```

- [ ] **Step 3: Push**

```bash
git push origin main
```

- [ ] **Step 4: Post-deploy prod spot-check**

```text
POST https://x402-validator-tools.onrender.com/audit-public
body: {"url":"https://example.com"}
```

Assert:
- `checks` includes `batch_settlement_requirements`
- `summary` denominator == `len(checks)` (engine + cold + batch)
- overall matches worst status among all checks

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Pure evaluate, no HTTP in unit path | T2 |
| cold 402 reuse / zero extra GET | T3 + T4 |
| GET fallback | T4 |
| Header precedence + case-insensitive | T1 |
| Field validation table (EVM) | T2 |
| payTo alias only | T2 |
| findings cap + findings_total | T2 |
| applicable tri-state | T2 |
| spec_ref 40-char commit | T2 |
| Wire validate + audit-public | T4 |
| aggregate overall/summary | already on main; T4 uses it |
| nine checks landing | T5 |
| Engine GET divergence risk (docs only) | no code; already in design doc |

## Placeholder scan

No TBD steps. Concrete code, commands, and file paths throughout.

## Type consistency

- Check dict keys: `check_name`, `status`, `message`, `details` (same as cold probe).
- `_run_audit` → `(report, probe_dict, batch_dict)`.
- Snapshot: `status_code: int`, `headers: Mapping`, `body: str`.

---

**Plan complete and saved to `docs/superpowers/plans/2026-08-08-batch-settlement-requirements.md`.**
