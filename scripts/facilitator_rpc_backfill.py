#!/usr/bin/env python3
"""Enrich facilitator event JSONL with Base RPC gas fields (Source B).

Idempotent: re-running on the same input dedupes by tx_hash (last write wins).

Usage:
  python scripts/facilitator_rpc_backfill.py \\
    --in data/facilitator_events.jsonl \\
    --out data/facilitator_merged.jsonl

Does not print private keys. Optional BASE_RPC_URL / ETH_USD_PRICE env vars.
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
    dedupe_events,
    fetch_tx_gas,
    load_manual_jsonl,
    merge_event,
    normalize_tx_hash,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backfill gas from Base RPC by tx_hash")
    p.add_argument("--in", dest="in_path", required=True, help="Source A JSONL")
    p.add_argument("--out", dest="out_path", required=True, help="Merged JSONL output")
    p.add_argument(
        "--rpc-url",
        default=None,
        help="JSON-RPC URL (default BASE_RPC_URL or public Base)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print summary only; do not write",
    )
    args = p.parse_args(argv)

    in_path = Path(args.in_path)
    if not in_path.is_file():
        print(f"Input not found: {in_path}", file=sys.stderr)
        return 1

    events = dedupe_events(load_manual_jsonl(in_path))
    # Second pass: if out exists, we still only emit unique tx_hash rows
    merged_by_tx: dict[str, dict] = {}

    for event in events:
        th = normalize_tx_hash(event.tx_hash)
        if not th:
            rec = merge_event(event, None)
            merged_by_tx[event.key()] = rec.to_dict()
            continue
        rpc = fetch_tx_gas(
            th,
            chain_id=event.chain_id,
            rpc_url=args.rpc_url,
            submitted_at=event.timestamp,
        )
        rec = merge_event(event, rpc)
        merged_by_tx[th] = rec.to_dict()

    rows = list(merged_by_tx.values())
    print(
        f"events_in={len(events)} unique_out={len(rows)} "
        f"(deduped by tx_hash)",
        file=sys.stderr,
    )

    if args.dry_run:
        return 0

    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=False) + "\n")
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
