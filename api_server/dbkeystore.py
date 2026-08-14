"""PostgreSQL-backed KeyStore (drop-in replacement for the JSON keystore).

Activated by setting ``DATABASE_URL``::

    DATABASE_URL=postgresql://user:pass@<neon-endpoint>:5432/x402

The public interface is identical to ``api_server.keystore.KeyStore``, so
application code keeps calling ``get_store()`` unchanged. The schema is
created idempotently on first boot (safe against an empty Neon database).

On top of the JSON store's behavior, this backend adds:

- ``record_audit(...)``          one row per served audit (no report payload)
- ``usage_this_month(key)``      audits served to ``key`` this calendar month
- ``quota_allows(key, plan_id)`` False once the plan's monthly quota is used
- ``audit_stats()``              live counters for the /open page

Requires ``psycopg[binary,pool] >= 3.2`` (see requirements.txt). All DB
errors in ``record_audit`` are swallowed on purpose: metrics must never
break a paying request.
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Any

from api_server.models import PLANS

# ---------------------------------------------------------------------------
# Schema (idempotent; each statement runs independently)
# ---------------------------------------------------------------------------

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS x402_api_keys (
        token       TEXT PRIMARY KEY,
        plan_id     TEXT NOT NULL,
        customer_id TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS x402_claims (
        session_id  TEXT PRIMARY KEY,
        plan_id     TEXT NOT NULL,
        api_key     TEXT NOT NULL REFERENCES x402_api_keys(token) ON DELETE CASCADE,
        customer_id TEXT,
        issued_at   TIMESTAMPTZ NOT NULL,
        claimed_at  TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS x402_audits (
        id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
        url         TEXT NOT NULL,
        mode        TEXT NOT NULL,
        overall     TEXT,
        latency_ms  DOUBLE PRECISION,
        caller_key  TEXT,
        caller_plan TEXT,
        source      TEXT NOT NULL DEFAULT 'api'
    )
    """,
    "CREATE INDEX IF NOT EXISTS x402_audits_caller_ts_idx ON x402_audits (caller_key, ts)",
    "CREATE INDEX IF NOT EXISTS x402_audits_ts_idx ON x402_audits (ts)",
)


def ensure_schema(conn) -> None:
    """Create tables/indexes if missing. Works on any psycopg3 connection."""
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)


def _iso(dt: Any) -> str | None:
    """Normalize a DB timestamp to the ISO shape the JSON store uses."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


class DBKeyStore:
    """PostgreSQL-backed key+claims store with usage accounting.

    Thread-safe and multi-process safe by construction (the database is the
    lock), which is exactly what multi-replica deployments need and what the
    JSON keystore cannot provide.
    """

    backend = "postgres"

    def __init__(
        self,
        database_url: str | None = None,
        *,
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        from psycopg_pool import ConnectionPool  # lazy: keep JSON mode dep-free

        conninfo = database_url or os.environ.get("DATABASE_URL", "")
        if not conninfo:
            raise RuntimeError(
                "DBKeyStore requires DATABASE_URL "
                "(e.g. postgresql://user:pass@host:5432/x402)"
            )
        self._pool = ConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            name="x402-keystore",
            open=True,
        )
        # Fail fast on boot if the schema cannot be created — a paid service
        # should not come up half-configured.
        with self._pool.connection() as conn:
            ensure_schema(conn)

    def close(self) -> None:
        self._pool.close()

    @property
    def pool(self):
        """Expose the connection pool so api_server.auth.UserStore can share
        it (one pool per process — Neon free tier limits connections)."""
        return self._pool

    # ----- key lookup (used by validation gate) -----

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __getitem__(self, key: str) -> str:
        plan = self.get(key)
        if plan is None:
            raise KeyError(key)
        return plan

    def get(self, key: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT plan_id FROM x402_api_keys WHERE token = %s", (key,)
            ).fetchone()
        return row[0] if row else None

    def all(self) -> dict[str, str]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT token, plan_id FROM x402_api_keys ORDER BY created_at"
            ).fetchall()
        return {token: plan for token, plan in rows}

    # ----- issuance -----

    def issue(
        self,
        plan_id: str,
        *,
        customer_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Mint a new random API key; atomically persist key (+claim)."""
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO x402_api_keys (token, plan_id, customer_id) "
                "VALUES (%s, %s, %s)",
                (token, plan_id, customer_id),
            )
            if session_id:
                conn.execute(
                    "INSERT INTO x402_claims "
                    "(session_id, plan_id, api_key, customer_id, issued_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (session_id, plan_id, token, customer_id,
                     datetime.now(timezone.utc)),
                )
        return token

    def revoke(self, key: str) -> bool:
        """Delete a key (claims cascade). Returns True if it existed."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM x402_api_keys WHERE token = %s", (key,)
            )
        return cur.rowcount > 0

    # ----- claim lookup (used by /success) -----

    def claim_by_session(self, session_id: str | None) -> dict | None:
        if not session_id:
            return None
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT plan_id, api_key, customer_id, issued_at, claimed_at "
                "FROM x402_claims WHERE session_id = %s",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "plan_id": row[0],
            "api_key": row[1],
            "customer_id": row[2],
            "issued_at": _iso(row[3]),
            "claimed_at": _iso(row[4]),
        }

    def mark_claimed(self, session_id: str) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "UPDATE x402_claims SET claimed_at = now() "
                "WHERE session_id = %s",
                (session_id,),
            )
        return cur.rowcount > 0

    def claims_all(self) -> dict[str, dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT session_id, plan_id, api_key, customer_id, "
                "issued_at, claimed_at FROM x402_claims ORDER BY issued_at"
            ).fetchall()
        return {
            row[0]: {
                "plan_id": row[1],
                "api_key": row[2],
                "customer_id": row[3],
                "issued_at": _iso(row[4]),
                "claimed_at": _iso(row[5]),
            }
            for row in rows
        }

    # ----- usage accounting (new vs JSON store) -----

    def record_audit(
        self,
        *,
        url: str,
        mode: str,
        overall: str | None = None,
        latency_ms: float | None = None,
        caller_key: str | None = None,
        caller_plan: str | None = None,
        source: str = "api",
    ) -> None:
        """Append one audit row. Best-effort: never raises."""
        try:
            with self._pool.connection() as conn:
                conn.execute(
                    "INSERT INTO x402_audits "
                    "(url, mode, overall, latency_ms, caller_key, "
                    "caller_plan, source) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (url, mode, overall, latency_ms, caller_key,
                     caller_plan, source),
                )
        except Exception as e:  # pragma: no cover - defensive
            # Structured when the API logger is configured; stderr fallback otherwise.
            try:
                from api_server.logging_config import get_logger

                get_logger().warning(
                    "record_audit_failed",
                    extra={
                        "event": "db.record_audit_failed",
                        "error_type": type(e).__name__,
                        "url": url,
                        "mode": mode,
                        "source": source,
                    },
                )
            except Exception:
                print(
                    f"[dbkeystore] record_audit failed (ignored): {e}",
                    file=sys.stderr,
                )

    def usage_this_month(self, key: str) -> int:
        """Audits served to ``key`` since the start of the current month."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT count(*) FROM x402_audits "
                "WHERE caller_key = %s AND ts >= date_trunc('month', now())",
                (key,),
            ).fetchone()
        return int(row[0]) if row else 0

    def quota_allows(self, key: str, plan_id: str | None) -> bool:
        """False once the plan's monthly audit quota is exhausted."""
        plan = PLANS.get(plan_id or "")
        if plan is None:
            return True  # auth gate handles unknown plans, not quotas
        return self.usage_this_month(key) < plan.requests_per_month

    def audit_stats(self) -> dict:
        """Live counters for the /open page (all-time + current month)."""
        with self._pool.connection() as conn:
            total_row = conn.execute(
                "SELECT count(*) FROM x402_audits"
            ).fetchone()
            month_row = conn.execute(
                "SELECT count(*) FROM x402_audits "
                "WHERE ts >= date_trunc('month', now())"
            ).fetchone()
            pass_row = conn.execute(
                "SELECT count(*) FROM x402_audits "
                "WHERE ts >= date_trunc('month', now()) AND overall = 'PASS'"
            ).fetchone()
        return {
            "total": int(total_row[0]) if total_row else 0,
            "this_month": int(month_row[0]) if month_row else 0,
            "pass_this_month": int(pass_row[0]) if pass_row else 0,
        }
