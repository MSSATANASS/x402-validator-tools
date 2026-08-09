"""Facilitator gas / wallet-reuse instrumentation (scaffolding).

Decoupled from ``/validate``: no settlement path exists in this repo yet.
Source A = app log / manual JSONL; Source B = Base RPC enrichment.
Chain always wins for gas_used / gas_price. Reuse is reported as
``{state, method}``, never a bare boolean.

See ``docs/facilitator_metrics_ingest.md``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

WALLET_HASH_PREFIX = "x402-wallet-v1:"
DEFAULT_CHAIN_ID = 8453
DEFAULT_BASE_RPC = "https://mainnet.base.org"
WEI_PER_ETH = 10**18


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str | datetime | None) -> datetime:
    """Parse ISO-8601 (with optional Z) into timezone-aware UTC datetime."""
    if value is None:
        return _utcnow()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_tx_hash(tx_hash: str | None) -> str | None:
    if tx_hash is None:
        return None
    h = tx_hash.strip().lower()
    if not h:
        return None
    if not h.startswith("0x"):
        h = "0x" + h
    return h


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def hash_wallet(address: str) -> str:
    """SHA-256 of ``x402-wallet-v1:{address.lower()}``.

    Does not store or return the address. Used for grouping / dedupe only —
    not privacy when ``tx_hash`` is also present (see docs).
    """
    if not address or not str(address).strip():
        raise ValueError("wallet address is required")
    addr = str(address).strip().lower()
    if addr.startswith("0x"):
        body = addr[2:]
    else:
        body = addr
        addr = "0x" + body
    # Accept 40-hex addresses; still hash any non-empty string for flexibility
    # in synthetic tests, but normalize 0x prefix when present.
    material = f"{WALLET_HASH_PREFIX}{addr}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class StateMethod:
    state: str
    method: str

    def to_dict(self) -> dict[str, str]:
        return {"state": self.state, "method": self.method}


@dataclass
class TxEvent:
    """Source A / manual ingest event (one submitted or recorded tx)."""

    tx_hash: str | None
    wallet_hash: str | None
    endpoint: str
    timestamp: datetime
    chain_id: int = DEFAULT_CHAIN_ID
    role: str = "agent"
    event_id: str | None = None
    income_usd: float | None = None
    gas_limit_estimate: int | None = None
    source_a: StateMethod = field(
        default_factory=lambda: StateMethod("submitted", "app_log")
    )
    extra: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        """Idempotency key: prefer tx_hash, else event_id."""
        th = normalize_tx_hash(self.tx_hash)
        if th:
            return th
        if self.event_id:
            return f"event:{self.event_id}"
        # Last resort stable key for incomplete rows
        return f"incomplete:{self.wallet_hash}:{self.endpoint}:{to_iso(self.timestamp)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "tx_hash": normalize_tx_hash(self.tx_hash),
            "wallet_hash": self.wallet_hash,
            "endpoint": self.endpoint,
            "timestamp": to_iso(self.timestamp),
            "chain_id": self.chain_id,
            "role": self.role,
            "event_id": self.event_id,
            "income_usd": self.income_usd,
            "gas_limit_estimate": self.gas_limit_estimate,
            "source_a": self.source_a.to_dict(),
            "extra": self.extra,
        }


@dataclass
class RpcEnrichment:
    """Source B — RPC / chain view for one tx_hash."""

    tx_hash: str
    source_b: StateMethod
    gas_used: int | None = None
    gas_price_wei: int | None = None
    gas_cost_wei: int | None = None
    gas_cost_native: float | None = None
    gas_cost_usd: float | None = None
    price_source: str = "unavailable"
    block_number: int | None = None
    from_address_hash: str | None = None
    to_address_hash: str | None = None
    queried_at: datetime = field(default_factory=_utcnow)
    rpc_host: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tx_hash": normalize_tx_hash(self.tx_hash),
            "source_b": self.source_b.to_dict(),
            "gas_used": self.gas_used,
            "gas_price_wei": self.gas_price_wei,
            "gas_cost_wei": self.gas_cost_wei,
            "gas_cost_native": self.gas_cost_native,
            "gas_cost_usd": self.gas_cost_usd,
            "price_source": self.price_source,
            "block_number": self.block_number,
            "from_address_hash": self.from_address_hash,
            "to_address_hash": self.to_address_hash,
            "queried_at": to_iso(self.queried_at),
            "rpc_host": self.rpc_host,
        }


@dataclass
class MergedTxRecord:
    tx_hash: str | None
    wallet_hash: str | None
    endpoint: str | None
    submitted_at: datetime | None
    role: str | None
    chain_id: int
    source_a: StateMethod | None
    source_b: StateMethod | None
    merge: StateMethod
    gas_used: int | None = None
    gas_price_wei: int | None = None
    gas_cost_wei: int | None = None
    gas_cost_native: float | None = None
    gas_cost_usd: float | None = None
    price_source: str = "unavailable"
    block_number: int | None = None
    gas_limit_estimate: int | None = None
    income_usd: float | None = None
    reuse: StateMethod | None = None
    count_1h: int | None = None
    count_24h: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tx_hash": normalize_tx_hash(self.tx_hash),
            "wallet_hash": self.wallet_hash,
            "endpoint": self.endpoint,
            "submitted_at": to_iso(self.submitted_at) if self.submitted_at else None,
            "role": self.role,
            "chain_id": self.chain_id,
            "source_a": self.source_a.to_dict() if self.source_a else None,
            "chain": {
                "gas_used": self.gas_used,
                "gas_price_wei": self.gas_price_wei,
                "gas_cost_wei": self.gas_cost_wei,
                "gas_cost_native": self.gas_cost_native,
                "gas_cost_usd": self.gas_cost_usd,
                "price_source": self.price_source,
                "block_number": self.block_number,
                "source_b": self.source_b.to_dict() if self.source_b else None,
            },
            "merge": self.merge.to_dict(),
            "gas_limit_estimate": self.gas_limit_estimate,
            "income_usd": self.income_usd,
            "reuse": {
                "count_1h": self.count_1h,
                "count_24h": self.count_24h,
                "state": self.reuse.state if self.reuse else None,
                "method": self.reuse.method if self.reuse else None,
            },
        }


@dataclass
class ReuseRow:
    endpoint: str
    wallet_hash: str
    count_1h: int
    count_24h: int
    state: str
    method: str
    tx_hashes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "wallet_hash": self.wallet_hash,
            "count_1h": self.count_1h,
            "count_24h": self.count_24h,
            "state": self.state,
            "method": self.method,
            "tx_hashes": self.tx_hashes,
        }


@dataclass
class FacilitatorBalance:
    period_start: datetime
    period_end: datetime
    period_label: str
    gas_paid_native: float
    gas_paid_usd: float | None
    income_usd: float
    net_usd: float | None
    net: StateMethod
    tx_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_label": self.period_label,
            "period_start": to_iso(self.period_start),
            "period_end": to_iso(self.period_end),
            "gas_paid_native": self.gas_paid_native,
            "gas_paid_usd": self.gas_paid_usd,
            "income_usd": self.income_usd,
            "net_usd": self.net_usd,
            "net": self.net.to_dict(),
            "tx_count": self.tx_count,
        }


# ---------------------------------------------------------------------------
# Source A — record + loaders
# ---------------------------------------------------------------------------


def record_tx_submitted(
    tx_hash: str,
    wallet_address: str,
    endpoint: str,
    *,
    chain_id: int = DEFAULT_CHAIN_ID,
    role: str = "agent",
    timestamp: datetime | None = None,
    income_usd: float | None = None,
    gas_limit_estimate: int | None = None,
    event_id: str | None = None,
) -> TxEvent:
    """Build a Source A event; hashes the address (never stores it)."""
    return TxEvent(
        tx_hash=normalize_tx_hash(tx_hash),
        wallet_hash=hash_wallet(wallet_address),
        endpoint=endpoint.strip(),
        timestamp=parse_timestamp(timestamp),
        chain_id=chain_id,
        role=role,
        event_id=event_id,
        income_usd=income_usd,
        gas_limit_estimate=gas_limit_estimate,
        source_a=StateMethod("submitted", "app_log"),
    )


def _event_from_mapping(row: Mapping[str, Any], *, default_method: str) -> TxEvent:
    tx_hash = normalize_tx_hash(row.get("tx_hash") or row.get("txHash"))
    wallet_hash = row.get("wallet_hash") or row.get("walletHash")
    wallet_address = row.get("wallet_address") or row.get("walletAddress")
    if not wallet_hash and wallet_address:
        wallet_hash = hash_wallet(str(wallet_address))
    endpoint = str(row.get("endpoint") or "").strip()
    if not endpoint:
        raise ValueError("endpoint is required")
    ts = parse_timestamp(row.get("timestamp") or row.get("submitted_at"))
    chain_id = int(row.get("chain_id") or row.get("chainId") or DEFAULT_CHAIN_ID)
    role = str(row.get("role") or "agent")
    event_id = row.get("event_id") or row.get("eventId")
    income = row.get("income_usd")
    if income is not None:
        income = float(income)
    gas_est = row.get("gas_limit_estimate")
    if gas_est is not None:
        gas_est = int(gas_est)

    ingest = row.get("ingest") or row.get("source_a") or {}
    if isinstance(ingest, Mapping) and ingest.get("state") and ingest.get("method"):
        source_a = StateMethod(str(ingest["state"]), str(ingest["method"]))
    else:
        source_a = StateMethod("recorded", default_method)

    return TxEvent(
        tx_hash=tx_hash,
        wallet_hash=str(wallet_hash) if wallet_hash else None,
        endpoint=endpoint,
        timestamp=ts,
        chain_id=chain_id,
        role=role,
        event_id=str(event_id) if event_id else None,
        income_usd=income,
        gas_limit_estimate=gas_est,
        source_a=source_a,
    )


def load_manual_jsonl(path: str | Path) -> list[TxEvent]:
    """Load JSONL events; last write wins per tx_hash (idempotent)."""
    path = Path(path)
    by_key: dict[str, TxEvent] = {}
    with path.open(encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {e}") from e
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{lineno}: expected object")
            event = _event_from_mapping(row, default_method="manual_jsonl")
            by_key[event.key()] = event
    return list(by_key.values())


def load_manual_csv(path: str | Path) -> list[TxEvent]:
    """Load CSV events; last write wins per tx_hash (idempotent)."""
    path = Path(path)
    by_key: dict[str, TxEvent] = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not any((v or "").strip() for v in row.values()):
                continue
            event = _event_from_mapping(row, default_method="manual_csv")
            by_key[event.key()] = event
    return list(by_key.values())


def dedupe_events(events: Iterable[TxEvent]) -> list[TxEvent]:
    """Dedupe by tx_hash / event key; last occurrence wins."""
    by_key: dict[str, TxEvent] = {}
    for e in events:
        by_key[e.key()] = e
    return list(by_key.values())


# ---------------------------------------------------------------------------
# Source B — RPC
# ---------------------------------------------------------------------------


def _hex_to_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value)
    if s in ("", "0x", "0x0"):
        return 0 if s.startswith("0x") else None
    return int(s, 16)


def _rpc_call(
    rpc_url: str,
    method: str,
    params: list[Any],
    *,
    timeout: float = 15.0,
) -> Any:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if "error" in payload and payload["error"]:
        err = payload["error"]
        raise RuntimeError(f"RPC error: {err}")
    return payload.get("result")


def _rpc_host(rpc_url: str) -> str:
    # host only — never credentials
    try:
        from urllib.parse import urlparse

        return urlparse(rpc_url).netloc or rpc_url
    except Exception:
        return rpc_url


def _apply_usd(gas_cost_native: float | None) -> tuple[float | None, str]:
    raw = os.environ.get("ETH_USD_PRICE") or os.environ.get("BASE_ETH_USD_PRICE")
    if raw is None or gas_cost_native is None:
        return None, "unavailable"
    try:
        price = float(raw)
    except ValueError:
        return None, "unavailable"
    return gas_cost_native * price, "env_manual"


def fetch_tx_gas(
    tx_hash: str,
    *,
    chain_id: int = DEFAULT_CHAIN_ID,
    rpc_url: str | None = None,
    submitted_at: datetime | None = None,
    now: datetime | None = None,
    timeout: float = 15.0,
    rpc_call: Callable[..., Any] | None = None,
) -> RpcEnrichment:
    """Query Base (or any) JSON-RPC for gas fields. Never raises for missing tx.

    ``rpc_call`` is injectable for tests: ``rpc_call(method, params) -> result``.
    """
    th = normalize_tx_hash(tx_hash)
    if not th:
        return RpcEnrichment(
            tx_hash="",
            source_b=StateMethod("not_found", "rpc_get_tx"),
            queried_at=now or _utcnow(),
        )

    url = rpc_url or os.environ.get("BASE_RPC_URL") or DEFAULT_BASE_RPC
    host = _rpc_host(url)
    call = rpc_call
    now = now or _utcnow()

    def _do(method: str, params: list[Any]) -> Any:
        if call is not None:
            return call(method, params)
        return _rpc_call(url, method, params, timeout=timeout)

    try:
        tx = _do("eth_getTransactionByHash", [th])
    except Exception:
        return RpcEnrichment(
            tx_hash=th,
            source_b=StateMethod("rpc_error", "rpc_call"),
            queried_at=now,
            rpc_host=host,
        )

    if tx is None:
        # If app recently submitted, treat as pending; else not_found
        state = "not_found"
        if submitted_at is not None:
            age = now - submitted_at
            if age <= timedelta(minutes=15):
                state = "pending"
        return RpcEnrichment(
            tx_hash=th,
            source_b=StateMethod(state, "rpc_get_tx"),
            queried_at=now,
            rpc_host=host,
        )

    block = tx.get("blockNumber") if isinstance(tx, dict) else None
    if block is None:
        return RpcEnrichment(
            tx_hash=th,
            source_b=StateMethod("pending", "rpc_get_tx"),
            gas_price_wei=_hex_to_int(tx.get("gasPrice") if isinstance(tx, dict) else None),
            queried_at=now,
            rpc_host=host,
        )

    try:
        receipt = _do("eth_getTransactionReceipt", [th])
    except Exception:
        return RpcEnrichment(
            tx_hash=th,
            source_b=StateMethod("rpc_error", "rpc_call"),
            queried_at=now,
            rpc_host=host,
        )

    if receipt is None:
        return RpcEnrichment(
            tx_hash=th,
            source_b=StateMethod("pending", "rpc_receipt"),
            queried_at=now,
            rpc_host=host,
        )

    gas_used = _hex_to_int(receipt.get("gasUsed"))
    # effectiveGasPrice (EIP-1559) preferred
    gas_price = _hex_to_int(receipt.get("effectiveGasPrice"))
    if gas_price is None and isinstance(tx, dict):
        gas_price = _hex_to_int(tx.get("gasPrice"))

    gas_cost_wei = None
    gas_cost_native = None
    if gas_used is not None and gas_price is not None:
        gas_cost_wei = gas_used * gas_price
        gas_cost_native = gas_cost_wei / WEI_PER_ETH

    gas_cost_usd, price_source = _apply_usd(gas_cost_native)

    status_raw = receipt.get("status")
    status_int = _hex_to_int(status_raw)
    if status_int == 0:
        sm = StateMethod("reverted", "rpc_receipt")
    else:
        sm = StateMethod("mined", "rpc_receipt")

    from_hash = None
    to_hash = None
    if receipt.get("from"):
        try:
            from_hash = hash_wallet(str(receipt["from"]))
        except ValueError:
            from_hash = None
    if receipt.get("to"):
        try:
            to_hash = hash_wallet(str(receipt["to"]))
        except ValueError:
            to_hash = None

    return RpcEnrichment(
        tx_hash=th,
        source_b=sm,
        gas_used=gas_used,
        gas_price_wei=gas_price,
        gas_cost_wei=gas_cost_wei,
        gas_cost_native=gas_cost_native,
        gas_cost_usd=gas_cost_usd,
        price_source=price_source,
        block_number=_hex_to_int(receipt.get("blockNumber")),
        from_address_hash=from_hash,
        to_address_hash=to_hash,
        queried_at=now,
        rpc_host=host,
    )


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def merge_event(
    app: TxEvent | None,
    rpc: RpcEnrichment | None,
) -> MergedTxRecord:
    """Merge A and B. Chain (B) always wins for gas_* fields."""
    tx_hash = None
    if app and app.tx_hash:
        tx_hash = normalize_tx_hash(app.tx_hash)
    if rpc and rpc.tx_hash:
        tx_hash = normalize_tx_hash(rpc.tx_hash) or tx_hash

    wallet_hash = app.wallet_hash if app else None
    endpoint = app.endpoint if app else None
    submitted_at = app.timestamp if app else None
    role = app.role if app else None
    chain_id = app.chain_id if app else DEFAULT_CHAIN_ID
    source_a = app.source_a if app else None
    income = app.income_usd if app else None
    gas_est = app.gas_limit_estimate if app else None

    source_b = rpc.source_b if rpc else None
    gas_used = rpc.gas_used if rpc else None
    gas_price = rpc.gas_price_wei if rpc else None
    gas_cost_wei = rpc.gas_cost_wei if rpc else None
    gas_cost_native = rpc.gas_cost_native if rpc else None
    gas_cost_usd = rpc.gas_cost_usd if rpc else None
    price_source = rpc.price_source if rpc else "unavailable"
    block_number = rpc.block_number if rpc else None

    # Derive wallet from RPC from_address_hash if A missing
    if not wallet_hash and rpc and rpc.from_address_hash:
        wallet_hash = rpc.from_address_hash
        if source_a is None:
            source_a = StateMethod("derived", "derived_from_rpc")

    has_a = app is not None
    has_b_gas = rpc is not None and rpc.gas_used is not None
    b_state = rpc.source_b.state if rpc else None

    if has_a and has_b_gas:
        merge = StateMethod("complete", "a_plus_b")
    elif has_a and not has_b_gas:
        if b_state in ("pending", "not_found", "rpc_error", None):
            merge = StateMethod("app_only", "await_rpc")
        else:
            merge = StateMethod("app_only", "await_rpc")
    elif not has_a and rpc is not None:
        merge = StateMethod("chain_only", "rpc_only")
    else:
        merge = StateMethod("unknown", "empty")

    # Conflict only if app asserted real gas_used via extra (rare)
    if (
        has_a
        and has_b_gas
        and app
        and app.extra.get("gas_used") is not None
        and int(app.extra["gas_used"]) != gas_used
    ):
        merge = StateMethod("conflict", "chain_wins")

    return MergedTxRecord(
        tx_hash=tx_hash,
        wallet_hash=wallet_hash,
        endpoint=endpoint,
        submitted_at=submitted_at,
        role=role,
        chain_id=chain_id,
        source_a=source_a,
        source_b=source_b,
        merge=merge,
        gas_used=gas_used,
        gas_price_wei=gas_price,
        gas_cost_wei=gas_cost_wei,
        gas_cost_native=gas_cost_native,
        gas_cost_usd=gas_cost_usd,
        price_source=price_source,
        block_number=block_number,
        gas_limit_estimate=gas_est,
        income_usd=income,
    )


def merge_all(
    events: Sequence[TxEvent],
    enrichments: Mapping[str, RpcEnrichment] | None = None,
    *,
    fetch: bool = False,
    fetch_fn: Callable[..., RpcEnrichment] | None = None,
) -> list[MergedTxRecord]:
    """Merge deduped events with optional enrichment map.

    If ``fetch`` is True, missing hashes are filled via ``fetch_fn`` or
    ``fetch_tx_gas`` (live RPC — do not enable in unit tests).
    """
    events = dedupe_events(events)
    enrichments = dict(enrichments or {})
    fn = fetch_fn or fetch_tx_gas
    out: list[MergedTxRecord] = []
    for e in events:
        th = normalize_tx_hash(e.tx_hash)
        rpc = enrichments.get(th) if th else None
        if rpc is None and fetch and th:
            rpc = fn(th, chain_id=e.chain_id, submitted_at=e.timestamp)
            enrichments[th] = rpc
        out.append(merge_event(e, rpc))
    return out


# ---------------------------------------------------------------------------
# Reuse counters
# ---------------------------------------------------------------------------


def reuse_state_method(count_1h: int, count_24h: int) -> StateMethod:
    """Map counts to {state, method} — never a boolean."""
    if count_24h <= 0:
        return StateMethod("unknown", "no_wallet")
    if count_1h >= 4:
        return StateMethod("reused", f"same_key_{count_1h}x")
    if count_1h >= 2:
        return StateMethod("reused", f"same_key_{count_1h}x")
    if count_24h >= 2 and count_1h <= 1:
        return StateMethod("reused", "same_key_24h")
    return StateMethod("unique", "first_seen")


def compute_reuse(
    events: Sequence[TxEvent],
    *,
    now: datetime | None = None,
) -> list[ReuseRow]:
    """Per (endpoint, wallet_hash) counts in 1h and 24h windows."""
    now = now or _utcnow()
    events = dedupe_events(events)
    # Group all events
    groups: dict[tuple[str, str], list[TxEvent]] = {}
    for e in events:
        if not e.wallet_hash:
            continue
        key = (e.endpoint, e.wallet_hash)
        groups.setdefault(key, []).append(e)

    rows: list[ReuseRow] = []
    window_1h = now - timedelta(hours=1)
    window_24h = now - timedelta(hours=24)

    for (endpoint, wallet_hash), group in sorted(groups.items()):
        in_1h = [e for e in group if e.timestamp >= window_1h]
        in_24h = [e for e in group if e.timestamp >= window_24h]
        # If all events are synthetic/old, still count relative to max timestamp
        # in group when nothing falls in window — better: use latest event time
        # as "now" for samples generated in the past.
        if not in_24h and group:
            anchor = max(e.timestamp for e in group)
            window_1h_a = anchor - timedelta(hours=1)
            window_24h_a = anchor - timedelta(hours=24)
            in_1h = [e for e in group if e.timestamp >= window_1h_a]
            in_24h = [e for e in group if e.timestamp >= window_24h_a]

        c1 = len(in_1h)
        c24 = len(in_24h)
        sm = reuse_state_method(c1, c24)
        hashes = [
            normalize_tx_hash(e.tx_hash) or e.key()
            for e in sorted(in_24h or group, key=lambda x: x.timestamp)
        ]
        rows.append(
            ReuseRow(
                endpoint=endpoint,
                wallet_hash=wallet_hash,
                count_1h=c1,
                count_24h=c24,
                state=sm.state,
                method=sm.method,
                tx_hashes=[h for h in hashes if h],
            )
        )
    return rows


def attach_reuse_to_merged(
    merged: Sequence[MergedTxRecord],
    reuse_rows: Sequence[ReuseRow],
) -> list[MergedTxRecord]:
    index = {(r.endpoint, r.wallet_hash): r for r in reuse_rows}
    out: list[MergedTxRecord] = []
    for m in merged:
        if m.endpoint and m.wallet_hash and (m.endpoint, m.wallet_hash) in index:
            r = index[(m.endpoint, m.wallet_hash)]
            m.count_1h = r.count_1h
            m.count_24h = r.count_24h
            m.reuse = StateMethod(r.state, r.method)
        elif not m.wallet_hash:
            m.reuse = StateMethod("unknown", "no_wallet")
            m.count_1h = 0
            m.count_24h = 0
        else:
            m.reuse = StateMethod("unique", "first_seen")
            m.count_1h = 1
            m.count_24h = 1
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# Balance
# ---------------------------------------------------------------------------


def compute_balance(
    merged: Sequence[MergedTxRecord],
    *,
    period_label: str = "all",
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    now: datetime | None = None,
) -> FacilitatorBalance:
    now = now or _utcnow()
    if period_label == "1h":
        period_end = period_end or now
        period_start = period_start or (period_end - timedelta(hours=1))
    elif period_label == "24h":
        period_end = period_end or now
        period_start = period_start or (period_end - timedelta(hours=24))
    else:
        times = [m.submitted_at for m in merged if m.submitted_at]
        period_start = period_start or (min(times) if times else now)
        period_end = period_end or (max(times) if times else now)

    rows = []
    for m in merged:
        ts = m.submitted_at
        if ts is None:
            rows.append(m)
            continue
        if period_start <= ts <= period_end:
            rows.append(m)

    gas_native = sum((m.gas_cost_native or 0.0) for m in rows)
    usd_parts = [m.gas_cost_usd for m in rows if m.gas_cost_usd is not None]
    gas_usd = sum(usd_parts) if usd_parts else None
    income = sum((m.income_usd or 0.0) for m in rows)

    if gas_usd is not None:
        net_usd = income - gas_usd
        if income == 0 and gas_usd > 0:
            net = StateMethod("deficit", "gas_only_no_income")
        elif net_usd < 0:
            net = StateMethod("deficit", "income_minus_gas")
        elif net_usd > 0:
            net = StateMethod("surplus", "income_minus_gas")
        else:
            net = StateMethod("unknown", "income_minus_gas")
    else:
        net_usd = None
        if income == 0:
            net = StateMethod("unknown", "gas_only_no_income")
        else:
            net = StateMethod("unknown", "income_minus_gas")

    return FacilitatorBalance(
        period_start=period_start,
        period_end=period_end,
        period_label=period_label,
        gas_paid_native=gas_native,
        gas_paid_usd=gas_usd,
        income_usd=income,
        net_usd=net_usd,
        net=net,
        tx_count=len(rows),
    )


# ---------------------------------------------------------------------------
# Store + export
# ---------------------------------------------------------------------------


class FacilitatorMetricsStore:
    """In-memory store with idempotent append by tx_hash."""

    def __init__(self) -> None:
        self._events: dict[str, TxEvent] = {}
        self._rpc: dict[str, RpcEnrichment] = {}

    def append(self, event: TxEvent) -> None:
        self._events[event.key()] = event

    def append_many(self, events: Iterable[TxEvent]) -> None:
        for e in events:
            self.append(e)

    def put_rpc(self, enrichment: RpcEnrichment) -> None:
        th = normalize_tx_hash(enrichment.tx_hash)
        if th:
            self._rpc[th] = enrichment

    def list_events(self, since: datetime | None = None) -> list[TxEvent]:
        events = list(self._events.values())
        if since is not None:
            events = [e for e in events if e.timestamp >= since]
        return sorted(events, key=lambda e: e.timestamp)

    def merge_all(self, *, fetch: bool = False) -> list[MergedTxRecord]:
        return merge_all(self.list_events(), self._rpc, fetch=fetch)

    def reuse_table(self, *, now: datetime | None = None) -> list[ReuseRow]:
        return compute_reuse(self.list_events(), now=now)

    def balance(
        self,
        period_label: str = "all",
        *,
        now: datetime | None = None,
    ) -> FacilitatorBalance:
        merged = attach_reuse_to_merged(self.merge_all(), self.reuse_table(now=now))
        return compute_balance(merged, period_label=period_label, now=now)


def build_report(
    events: Sequence[TxEvent],
    enrichments: Mapping[str, RpcEnrichment] | None = None,
    *,
    now: datetime | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """Full exportable report dict."""
    wall_now = now or _utcnow()
    events = dedupe_events(events)
    merged = merge_all(events, enrichments)

    # Synthetic / offline samples: analyze reuse + balance relative to the
    # batch's latest event so 1h windows are meaningful. Live ingest uses
    # wall-clock ``now``.
    if events and synthetic:
        analysis_now = max(e.timestamp for e in events)
    elif events:
        analysis_now = wall_now
    else:
        analysis_now = wall_now

    reuse = compute_reuse(events, now=analysis_now)
    merged = attach_reuse_to_merged(merged, reuse)

    bal_1h = compute_balance(merged, period_label="1h", now=analysis_now)
    bal_24h = compute_balance(merged, period_label="24h", now=analysis_now)
    bal_all = compute_balance(merged, period_label="all", now=analysis_now)

    by_endpoint: dict[str, Any] = {}
    by_wallet: dict[str, Any] = {}

    for m in merged:
        ep = m.endpoint or "_unknown"
        wh = m.wallet_hash or "_unknown"
        ep_bucket = by_endpoint.setdefault(
            ep,
            {
                "tx_count": 0,
                "gas_cost_native_sum": 0.0,
                "wallets": {},
            },
        )
        ep_bucket["tx_count"] += 1
        ep_bucket["gas_cost_native_sum"] += m.gas_cost_native or 0.0
        if m.wallet_hash:
            w = ep_bucket["wallets"].setdefault(
                m.wallet_hash,
                {
                    "count_1h": m.count_1h,
                    "count_24h": m.count_24h,
                    "state": m.reuse.state if m.reuse else None,
                    "method": m.reuse.method if m.reuse else None,
                },
            )
            # keep reuse stats from table
            w["count_1h"] = m.count_1h
            w["count_24h"] = m.count_24h
            if m.reuse:
                w["state"] = m.reuse.state
                w["method"] = m.reuse.method

        wh_bucket = by_wallet.setdefault(
            wh,
            {
                "tx_count": 0,
                "gas_cost_native_sum": 0.0,
                "endpoints": {},
            },
        )
        wh_bucket["tx_count"] += 1
        wh_bucket["gas_cost_native_sum"] += m.gas_cost_native or 0.0
        if m.endpoint:
            wh_bucket["endpoints"][m.endpoint] = {
                "count_1h": m.count_1h,
                "count_24h": m.count_24h,
                "state": m.reuse.state if m.reuse else None,
                "method": m.reuse.method if m.reuse else None,
            }

    # Overlay reuse table for accuracy
    for r in reuse:
        if r.endpoint in by_endpoint and r.wallet_hash in by_endpoint[r.endpoint]["wallets"]:
            by_endpoint[r.endpoint]["wallets"][r.wallet_hash].update(
                {
                    "count_1h": r.count_1h,
                    "count_24h": r.count_24h,
                    "state": r.state,
                    "method": r.method,
                }
            )

    pending_rpc = sum(
        1
        for m in merged
        if m.merge.state == "app_only"
        or (m.source_b and m.source_b.state in ("pending", "not_found"))
    )
    enriched = sum(1 for m in merged if m.gas_used is not None)

    return {
        "generated_at": to_iso(wall_now),
        "analysis_now": to_iso(analysis_now),
        "windows": {"1h": True, "24h": True},
        "transactions": [m.to_dict() for m in merged],
        "by_endpoint": by_endpoint,
        "by_wallet_hash": by_wallet,
        "reuse": [r.to_dict() for r in reuse],
        "facilitator_balance": {
            "1h": bal_1h.to_dict(),
            "24h": bal_24h.to_dict(),
            "all": bal_all.to_dict(),
        },
        "data_quality": {
            "source_a_count": len(events),
            "source_b_enriched": enriched,
            "pending_rpc": pending_rpc,
            "synthetic": synthetic,
            "unique_tx_hashes": len(
                {normalize_tx_hash(e.tx_hash) for e in events if e.tx_hash}
            ),
        },
    }


def export_report(
    report: Mapping[str, Any],
    *,
    json_path: str | Path,
    csv_dir: str | Path | None = None,
) -> None:
    """Write JSON report and optional CSV pair (tx + reuse)."""
    json_path = Path(json_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=False)
        f.write("\n")

    if csv_dir is None:
        csv_dir = json_path.parent
    csv_dir = Path(csv_dir)
    csv_dir.mkdir(parents=True, exist_ok=True)

    stem = json_path.stem
    tx_csv = csv_dir / f"{stem}_tx.csv"
    reuse_csv = csv_dir / f"{stem}_reuse.csv"

    tx_fields = [
        "tx_hash",
        "wallet_hash",
        "endpoint",
        "submitted_at",
        "role",
        "chain_id",
        "gas_used",
        "gas_price_wei",
        "gas_cost_native",
        "gas_cost_usd",
        "income_usd",
        "merge_state",
        "merge_method",
        "source_b_state",
        "source_b_method",
        "count_1h",
        "count_24h",
        "reuse_state",
        "reuse_method",
    ]
    with tx_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=tx_fields)
        w.writeheader()
        for t in report.get("transactions") or []:
            chain = t.get("chain") or {}
            reuse = t.get("reuse") or {}
            merge = t.get("merge") or {}
            sb = chain.get("source_b") or {}
            w.writerow(
                {
                    "tx_hash": t.get("tx_hash"),
                    "wallet_hash": t.get("wallet_hash"),
                    "endpoint": t.get("endpoint"),
                    "submitted_at": t.get("submitted_at"),
                    "role": t.get("role"),
                    "chain_id": t.get("chain_id"),
                    "gas_used": chain.get("gas_used"),
                    "gas_price_wei": chain.get("gas_price_wei"),
                    "gas_cost_native": chain.get("gas_cost_native"),
                    "gas_cost_usd": chain.get("gas_cost_usd"),
                    "income_usd": t.get("income_usd"),
                    "merge_state": merge.get("state"),
                    "merge_method": merge.get("method"),
                    "source_b_state": sb.get("state"),
                    "source_b_method": sb.get("method"),
                    "count_1h": reuse.get("count_1h"),
                    "count_24h": reuse.get("count_24h"),
                    "reuse_state": reuse.get("state"),
                    "reuse_method": reuse.get("method"),
                }
            )

    reuse_fields = [
        "endpoint",
        "wallet_hash",
        "count_1h",
        "count_24h",
        "state",
        "method",
        "tx_hashes",
    ]
    with reuse_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=reuse_fields)
        w.writeheader()
        for r in report.get("reuse") or []:
            w.writerow(
                {
                    "endpoint": r.get("endpoint"),
                    "wallet_hash": r.get("wallet_hash"),
                    "count_1h": r.get("count_1h"),
                    "count_24h": r.get("count_24h"),
                    "state": r.get("state"),
                    "method": r.get("method"),
                    "tx_hashes": "|".join(r.get("tx_hashes") or []),
                }
            )


def load_merged_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Load previously merged JSONL (one MergedTxRecord dict per line)."""
    path = Path(path)
    by_tx: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            th = normalize_tx_hash(row.get("tx_hash"))
            key = th or row.get("event_id") or json.dumps(row, sort_keys=True)
            by_tx[key] = row
    return list(by_tx.values())
