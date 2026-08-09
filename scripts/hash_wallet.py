#!/usr/bin/env python3
"""CLI: print wallet_hash for a public address (never logs the address to a file).

Usage:
  python scripts/hash_wallet.py 0xYourAddress
  python -m scripts.hash_wallet 0xYourAddress
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running without install: repo root on path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api_server.facilitator_metrics import hash_wallet  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Hash an EVM address for facilitator_metrics JSONL (grouping only)."
    )
    p.add_argument("address", help="Public address (0x…); never a private key")
    args = p.parse_args(argv)
    if "private" in args.address.lower() or len(args.address.replace("0x", "")) == 64:
        # 32-byte hex looks like a private key — refuse
        if not args.address.startswith("0x") or len(args.address) == 66:
            # 66 char 0x+64 hex is private key length; addresses are 42
            if len(args.address) >= 64:
                print(
                    "Refusing input that looks like a private key. "
                    "Pass a 20-byte public address (0x + 40 hex).",
                    file=sys.stderr,
                )
                return 2
    try:
        print(hash_wallet(args.address))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
