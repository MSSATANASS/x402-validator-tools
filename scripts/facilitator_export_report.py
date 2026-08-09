#!/usr/bin/env python3
"""Build JSON + CSV facilitator metrics report from JSONL events.

Idempotent: input is deduped by tx_hash so reprocessing the same file
does not inflate reuse counts.

Usage:
  python scripts/facilitator_export_report.py \\
    --in examples/facilitator_events.sample.jsonl \\
    --out reports/facilitator_metrics_sample.json \\
    --synthetic

Optional: --merged data/facilitator_merged.jsonl to attach chain gas from B.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api_server.facilitator_metrics import (  # noqa: E402
    RpcEnrichment,
    StateMethod,
    build_report,
    dedupe_events,
    export_report,
    load_manual_jsonl,
    load_merged_jsonl,
    normalize_tx_hash,
    parse_timestamp,
)


def _enrichment_from_merged_row(row: dict) -> RpcEnrichment | None:
    """Rebuild RpcEnrichment from a merged JSONL line (chain section)."""
    th = normalize_tx_hash(row.get("tx_hash"))
    if not th:
        return None
    chain = row.get("chain") or {}
    sb = chain.get("source_b") or row.get("source_b") or {}
    if not sb and chain.get("gas_used") is None:
        return None
    state = (sb or {}).get("state") or (
        "mined" if chain.get("gas_used") is not None else "not_found"
    )
    method = (sb or {}).get("method") or "rpc_receipt"
    return RpcEnrichment(
        tx_hash=th,
        source_b=StateMethod(state, method),
        gas_used=chain.get("gas_used"),
        gas_price_wei=chain.get("gas_price_wei"),
        gas_cost_wei=chain.get("gas_cost_wei"),
        gas_cost_native=chain.get("gas_cost_native"),
        gas_cost_usd=chain.get("gas_cost_usd"),
        price_source=chain.get("price_source") or "unavailable",
        block_number=chain.get("block_number"),
        queried_at=parse_timestamp(row.get("queried_at") or None),
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export facilitator metrics JSON/CSV")
    p.add_argument("--in", dest="in_path", required=True, help="Source A JSONL")
    p.add_argument(
        "--out",
        dest="out_path",
        required=True,
        help="Output JSON path (CSV written alongside)",
    )
    p.add_argument(
        "--merged",
        dest="merged_path",
        default=None,
        help="Optional merged JSONL from facilitator_rpc_backfill.py",
    )
    p.add_argument(
        "--synthetic",
        action="store_true",
        help="Mark data_quality.synthetic=true",
    )
    p.add_argument(
        "--with-sample-gas",
        action="store_true",
        help="Attach deterministic synthetic gas for sample demos (no RPC)",
    )
    args = p.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.is_file():
        print(f"Input not found: {in_path}", file=sys.stderr)
        return 1

    events = dedupe_events(load_manual_jsonl(in_path))
    # Load twice and merge to prove idempotency of dedupe path
    events = dedupe_events(events + load_manual_jsonl(in_path))

    enrichments: dict[str, RpcEnrichment] = {}
    if args.merged_path:
        for row in load_merged_jsonl(args.merged_path):
            enr = _enrichment_from_merged_row(row)
            if enr and enr.tx_hash:
                enrichments[normalize_tx_hash(enr.tx_hash) or enr.tx_hash] = enr

    if args.with_sample_gas:
        # Deterministic gas for synthetic business review (no RPC)
        for i, e in enumerate(events):
            th = normalize_tx_hash(e.tx_hash)
            if not th or th in enrichments:
                continue
            gas_used = 85_000 + (i * 1_000)
            gas_price = 100_000_000  # 0.1 gwei
            cost_wei = gas_used * gas_price
            enrichments[th] = RpcEnrichment(
                tx_hash=th,
                source_b=StateMethod("mined", "synthetic_sample"),
                gas_used=gas_used,
                gas_price_wei=gas_price,
                gas_cost_wei=cost_wei,
                gas_cost_native=cost_wei / 10**18,
                gas_cost_usd=None,
                price_source="unavailable",
                block_number=20_000_000 + i,
            )

    report = build_report(
        events,
        enrichments,
        synthetic=args.synthetic or args.with_sample_gas,
    )
    out_path = Path(args.out_path)
    export_report(report, json_path=out_path, csv_dir=out_path.parent)
    print(
        f"wrote {out_path} + {out_path.stem}_tx.csv + {out_path.stem}_reuse.csv "
        f"(unique_tx={report['data_quality']['unique_tx_hashes']})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
