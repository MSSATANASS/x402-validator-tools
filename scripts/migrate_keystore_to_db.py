#!/usr/bin/env python3
"""One-shot migration: JSON keystore -> PostgreSQL / Neon.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/x402 \
        python scripts/migrate_keystore_to_db.py [path/to/api_keys.json]

Defaults to ``API_KEYS_FILE`` or ``./api_keys.json``. Idempotent: rows that
already exist in the database are left untouched (ON CONFLICT DO NOTHING),
so it is safe to re-run. Prints a summary and exits 0 on success.

Typical cutover sequence (no downtime):
    1. Provision the Neon database and set ``DATABASE_URL`` on the Fly.io app.
    2. Export the legacy ``api_keys.json`` from its last durable location.
    3. Run this script locally against that export.
    4. Verify: compare `GET /admin/keys` output before/after.
    5. Deploy the Fly.io release, validate health and Stripe test webhooks, then
       retire the legacy deployment only after the rollback window closes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running straight from a repo checkout without pip install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api_server.keystore import _load  # noqa: E402


def main() -> int:
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        print("error: DATABASE_URL is not set.", file=sys.stderr)
        return 2

    if len(sys.argv) > 1:
        json_path = Path(sys.argv[1])
    else:
        json_path = Path(os.environ.get("API_KEYS_FILE", "api_keys.json"))

    if not json_path.exists():
        print(f"No JSON keystore at {json_path}; nothing to migrate.")
        return 0

    data = _load(json_path)
    keys = data["keys"]
    claims = data["claims"]

    import psycopg
    from api_server.dbkeystore import ensure_schema

    with psycopg.connect(db_url) as conn:
        ensure_schema(conn)

        inserted_keys = 0
        for token, plan_id in keys.items():
            cur = conn.execute(
                "INSERT INTO x402_api_keys (token, plan_id) "
                "VALUES (%s, %s) ON CONFLICT (token) DO NOTHING",
                (token, plan_id),
            )
            inserted_keys += cur.rowcount or 0

        inserted_claims = 0
        skipped_claims = 0
        for session_id, claim in claims.items():
            api_key = claim.get("api_key")
            if not api_key or api_key not in keys:
                skipped_claims += 1
                continue
            cur = conn.execute(
                "INSERT INTO x402_claims "
                "(session_id, plan_id, api_key, customer_id, issued_at, claimed_at) "
                "VALUES (%s, %s, %s, %s, COALESCE(%s, now()), %s) "
                "ON CONFLICT (session_id) DO NOTHING",
                (
                    session_id,
                    claim.get("plan_id"),
                    api_key,
                    claim.get("customer_id"),
                    claim.get("issued_at"),
                    claim.get("claimed_at"),
                ),
            )
            inserted_claims += cur.rowcount or 0

        conn.commit()

    # Never echo the full DSN (may contain a password).
    safe_target = db_url.split("@")[-1]
    print(f"Keys:   {inserted_keys} inserted / {len(keys)} in JSON -> {safe_target}")
    print(f"Claims: {inserted_claims} inserted / {len(claims)} in JSON"
          + (f" ({skipped_claims} skipped: dangling api_key)" if skipped_claims else ""))
    print("Done. Verify with GET /admin/keys, then switch traffic.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
