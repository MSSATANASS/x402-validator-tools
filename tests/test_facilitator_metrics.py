"""Unit tests for api_server.facilitator_metrics (no live RPC, no existing-suite edits)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api_server.facilitator_metrics import (
    FacilitatorMetricsStore,
    RpcEnrichment,
    StateMethod,
    TxEvent,
    attach_reuse_to_merged,
    build_report,
    compute_balance,
    compute_reuse,
    dedupe_events,
    export_report,
    fetch_tx_gas,
    hash_wallet,
    load_manual_csv,
    load_manual_jsonl,
    merge_all,
    merge_event,
    normalize_tx_hash,
    record_tx_submitted,
    reuse_state_method,
)

UTC = timezone.utc
SAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "facilitator_events.sample.jsonl"
)


def test_hash_wallet_stable_and_case_insensitive():
    a = "0xAbcDef0123456789AbcDef0123456789AbcDef01"
    b = "0xabcdef0123456789abcdef0123456789abcdef01"
    assert hash_wallet(a) == hash_wallet(b)
    assert len(hash_wallet(a)) == 64
    assert hash_wallet(a) != a.lower()


def test_hash_wallet_refuses_empty():
    with pytest.raises(ValueError):
        hash_wallet("  ")


def test_record_tx_submitted_hashes_address_not_stored():
    addr = "0x1111111111111111111111111111111111111111"
    ev = record_tx_submitted(
        "0x" + "ab" * 32,
        addr,
        "https://merchant.example/x",
    )
    assert ev.wallet_hash == hash_wallet(addr)
    blob = json.dumps(ev.to_dict())
    assert addr.lower() not in blob
    assert "private" not in blob


def test_load_jsonl_dedupes_by_tx_hash(tmp_path: Path):
    p = tmp_path / "e.jsonl"
    line = {
        "tx_hash": "0x" + "11" * 32,
        "wallet_hash": "aa" * 32,
        "endpoint": "https://e.example/a",
        "timestamp": "2026-08-09T17:00:00Z",
    }
    line2 = dict(line)
    line2["timestamp"] = "2026-08-09T18:00:00Z"
    p.write_text(
        json.dumps(line) + "\n" + json.dumps(line2) + "\n",
        encoding="utf-8",
    )
    events = load_manual_jsonl(p)
    assert len(events) == 1
    assert events[0].timestamp.hour == 18


def test_load_csv_and_jsonl_roundtrip(tmp_path: Path):
    csv_path = tmp_path / "e.csv"
    tx = "0x" + ("22" * 32)
    wh = "bb" * 32
    csv_path.write_text(
        "tx_hash,wallet_hash,endpoint,timestamp,chain_id,role\n"
        f"{tx},{wh},https://e.example/b,2026-08-09T17:00:00Z,8453,agent\n",
        encoding="utf-8",
    )
    events = load_manual_csv(csv_path)
    assert len(events) == 1
    assert events[0].endpoint.endswith("/b")
    assert events[0].wallet_hash == wh


def test_dedupe_events_idempotent():
    base = datetime(2026, 8, 9, 17, 0, tzinfo=UTC)
    e1 = TxEvent(
        tx_hash="0x" + "33" * 32,
        wallet_hash="cc" * 32,
        endpoint="https://e.example",
        timestamp=base,
    )
    e2 = TxEvent(
        tx_hash="0x" + "33" * 32,
        wallet_hash="cc" * 32,
        endpoint="https://e.example",
        timestamp=base + timedelta(minutes=1),
    )
    assert len(dedupe_events([e1, e2, e1])) == 1


def test_fetch_tx_gas_pending_and_mined_mocked():
    th = "0x" + "44" * 32

    def rpc_pending(method, params):
        if method == "eth_getTransactionByHash":
            return {"hash": th, "blockNumber": None, "gasPrice": "0x5f5e100"}
        return None

    pending = fetch_tx_gas(th, rpc_call=rpc_pending)
    assert pending.source_b.state == "pending"
    assert pending.gas_used is None

    def rpc_mined(method, params):
        if method == "eth_getTransactionByHash":
            return {
                "hash": th,
                "blockNumber": "0x10",
                "gasPrice": "0x3b9aca00",
                "from": "0x2222222222222222222222222222222222222222",
            }
        if method == "eth_getTransactionReceipt":
            return {
                "status": "0x1",
                "gasUsed": "0x5208",
                "effectiveGasPrice": "0x3b9aca00",
                "blockNumber": "0x10",
                "from": "0x2222222222222222222222222222222222222222",
                "to": "0x3333333333333333333333333333333333333333",
            }
        return None

    mined = fetch_tx_gas(th, rpc_call=rpc_mined)
    assert mined.source_b == StateMethod("mined", "rpc_receipt")
    assert mined.gas_used == 0x5208
    assert mined.gas_price_wei == 0x3B9ACA00
    assert mined.gas_cost_wei == 0x5208 * 0x3B9ACA00
    assert mined.from_address_hash == hash_wallet(
        "0x2222222222222222222222222222222222222222"
    )


def test_fetch_tx_gas_not_found_and_rpc_error():
    th = "0x" + "55" * 32

    def rpc_null(method, params):
        return None

    nf = fetch_tx_gas(th, rpc_call=rpc_null)
    assert nf.source_b.state == "not_found"

    def rpc_boom(method, params):
        raise RuntimeError("network down")

    err = fetch_tx_gas(th, rpc_call=rpc_boom)
    assert err.source_b == StateMethod("rpc_error", "rpc_call")


def test_merge_chain_wins_gas():
    app = record_tx_submitted(
        "0x" + "66" * 32,
        "0x4444444444444444444444444444444444444444",
        "https://m.example",
        gas_limit_estimate=120000,
    )
    app.extra["gas_used"] = 1  # wrong claim
    rpc = RpcEnrichment(
        tx_hash=app.tx_hash or "",
        source_b=StateMethod("mined", "rpc_receipt"),
        gas_used=90000,
        gas_price_wei=1_000_000_000,
        gas_cost_wei=90_000 * 1_000_000_000,
        gas_cost_native=0.00009,
    )
    m = merge_event(app, rpc)
    assert m.gas_used == 90000
    assert m.merge.state == "conflict"
    assert m.merge.method == "chain_wins"
    assert m.gas_limit_estimate == 120000


def test_merge_app_only_await_rpc():
    app = record_tx_submitted(
        "0x" + "77" * 32,
        "0x5555555555555555555555555555555555555555",
        "https://m.example",
    )
    m = merge_event(app, None)
    assert m.merge == StateMethod("app_only", "await_rpc")


def test_reuse_same_key_4x():
    base = datetime(2026, 8, 9, 17, 0, tzinfo=UTC)
    wh = "aa" * 32
    ep = "https://merchant.example/api/premium"
    events = [
        TxEvent(
            tx_hash="0x" + f"{i:02x}" * 32,
            wallet_hash=wh,
            endpoint=ep,
            timestamp=base + timedelta(minutes=i * 5),
        )
        for i in range(4)
    ]
    rows = compute_reuse(events, now=base + timedelta(hours=1))
    assert len(rows) == 1
    assert rows[0].count_1h == 4
    assert rows[0].state == "reused"
    assert rows[0].method == "same_key_4x"


def test_reuse_state_method_table():
    assert reuse_state_method(1, 1) == StateMethod("unique", "first_seen")
    assert reuse_state_method(2, 2).method == "same_key_2x"
    assert reuse_state_method(1, 3) == StateMethod("reused", "same_key_24h")


def test_store_append_idempotent():
    store = FacilitatorMetricsStore()
    e = record_tx_submitted(
        "0x" + "88" * 32,
        "0x6666666666666666666666666666666666666666",
        "https://m.example",
    )
    store.append(e)
    store.append(e)
    assert len(store.list_events()) == 1


def test_build_report_and_export(tmp_path: Path):
    assert SAMPLE.is_file()
    events = load_manual_jsonl(SAMPLE)
    # double-load must not inflate
    events = dedupe_events(events + load_manual_jsonl(SAMPLE))
    assert len(events) == 9

    enrichments = {}
    for i, e in enumerate(events):
        th = normalize_tx_hash(e.tx_hash)
        assert th
        gas_used = 85_000 + i * 1000
        gas_price = 100_000_000
        enrichments[th] = RpcEnrichment(
            tx_hash=th,
            source_b=StateMethod("mined", "synthetic_sample"),
            gas_used=gas_used,
            gas_price_wei=gas_price,
            gas_cost_wei=gas_used * gas_price,
            gas_cost_native=(gas_used * gas_price) / 10**18,
            block_number=1 + i,
        )

    report = build_report(events, enrichments, synthetic=True)
    assert report["data_quality"]["synthetic"] is True
    assert report["data_quality"]["unique_tx_hashes"] == 9
    assert report["data_quality"]["source_a_count"] == 9

    reuse = { (r["endpoint"], r["wallet_hash"]): r for r in report["reuse"] }
    premium = "https://merchant.example/api/premium"
    heavy = "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0"
    assert reuse[(premium, heavy)]["count_1h"] == 4
    assert reuse[(premium, heavy)]["method"] == "same_key_4x"
    assert reuse[(premium, heavy)]["state"] == "reused"

    out = tmp_path / "report.json"
    export_report(report, json_path=out, csv_dir=tmp_path)
    assert out.is_file()
    assert (tmp_path / "report_tx.csv").is_file()
    assert (tmp_path / "report_reuse.csv").is_file()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert len(loaded["transactions"]) == 9


def test_balance_gas_only_no_income():
    base = datetime(2026, 8, 9, 17, 0, tzinfo=UTC)
    events = [
        TxEvent(
            tx_hash="0x" + "99" * 32,
            wallet_hash="ee" * 32,
            endpoint="https://e.example",
            timestamp=base,
            income_usd=0,
        )
    ]
    rpc = {
        "0x" + "99" * 32: RpcEnrichment(
            tx_hash="0x" + "99" * 32,
            source_b=StateMethod("mined", "rpc_receipt"),
            gas_used=1000,
            gas_price_wei=10**9,
            gas_cost_wei=1000 * 10**9,
            gas_cost_native=0.000001,
            gas_cost_usd=0.003,
            price_source="env_manual",
        )
    }
    merged = attach_reuse_to_merged(merge_all(events, rpc), compute_reuse(events))
    bal = compute_balance(merged, period_label="all")
    assert bal.net.state == "deficit"
    assert bal.net.method == "gas_only_no_income"
    assert bal.gas_paid_usd == pytest.approx(0.003)
