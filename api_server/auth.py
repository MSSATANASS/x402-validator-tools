"""User accounts: argon2id passwords, server-side sessions, per-user keys.

Only active when ``DATABASE_URL`` is set (Neon-backed keystore). Without a
database, ``get_user_store()`` returns None and the auth routes answer 503.

Security notes:
- Passwords are hashed with argon2id; plaintext is never stored or logged.
- Session tokens are persisted ONLY as sha256 hashes: a DB leak does not
  allow impersonation.
- A user's API keys are referenced in HTML by ``kid`` (first 12 hex chars
  of sha256(token)); the raw token is shown exactly once, at creation.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher

SESSION_COOKIE = "x402_session"
SESSION_TTL_DAYS = 30
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 200
KID_LEN = 12

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hasher = PasswordHasher()
_dummy_hash: str | None = None  # lazy; equalizes login timing


class DuplicateEmail(Exception):
    """Raised by create_user when the (normalized) email already exists."""


# ---------------------------------------------------------------------------
# Primitives (pure — no DB)
# ---------------------------------------------------------------------------


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return len(email) <= 320 and bool(_EMAIL_RE.match(email))


def is_valid_password(password: str) -> bool:
    return PASSWORD_MIN_LEN <= len(password or "") <= PASSWORD_MAX_LEN


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    """True only for a matching argon2 hash. Never raises."""
    try:
        return _hasher.verify(stored_hash, password)
    except Exception:
        return False


def kid_for_token(token: str) -> str:
    """Public key identifier: sha256(token) hex prefix. Safe to render."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:KID_LEN]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Schema (idempotent; runs on UserStore boot. Assumes the keystore schema
# from dbkeystore.ensure_schema already exists — always true because a
# UserStore is only built from a live DBKeyStore's pool.)
# ---------------------------------------------------------------------------

AUTH_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS x402_users (
        id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        email              TEXT NOT NULL,
        password_hash      TEXT NOT NULL,
        plan_id            TEXT NOT NULL DEFAULT 'free',
        stripe_customer_id TEXT,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS x402_users_email_idx "
        "ON x402_users (lower(email))"
    ),
    """
    CREATE TABLE IF NOT EXISTS x402_sessions (
        token_hash TEXT PRIMARY KEY,
        user_id    BIGINT NOT NULL REFERENCES x402_users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    (
        "ALTER TABLE x402_api_keys ADD COLUMN IF NOT EXISTS user_id BIGINT "
        "REFERENCES x402_users(id) ON DELETE SET NULL"
    ),
    (
        "CREATE INDEX IF NOT EXISTS x402_api_keys_user_idx "
        "ON x402_api_keys (user_id)"
    ),
)


class UserStore:
    """Users, sessions and per-user API keys on the shared Neon pool."""

    def __init__(self, pool) -> None:
        self._pool = pool
        with self._pool.connection() as conn:
            for stmt in AUTH_SCHEMA_STATEMENTS:
                conn.execute(stmt)

    # ----- users -----

    def create_user(self, email: str, password: str) -> int:
        from psycopg.errors import UniqueViolation

        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    "INSERT INTO x402_users (email, password_hash) "
                    "VALUES (%s, %s) RETURNING id",
                    (normalize_email(email), hash_password(password)),
                ).fetchone()
        except UniqueViolation:
            raise DuplicateEmail(email)
        return int(row[0])

    def get_user(self, user_id: int) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT id, email, plan_id, stripe_customer_id, created_at "
                "FROM x402_users WHERE id = %s",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "email": row[1], "plan_id": row[2],
            "stripe_customer_id": row[3], "created_at": row[4],
        }

    def authenticate(self, email: str, password: str) -> int | None:
        """Return the user id on success, else None. The failure path is
        identical whether the email exists or not (no user enumeration)."""
        global _dummy_hash
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT id, password_hash FROM x402_users "
                "WHERE lower(email) = lower(%s)",
                (email,),
            ).fetchone()
        if row is None:
            if _dummy_hash is None:
                _dummy_hash = hash_password("timing-equalization-dummy")
            verify_password(_dummy_hash, password)  # burn equal CPU time
            return None
        if not verify_password(row[1], password):
            return None
        return int(row[0])

    def set_plan(self, user_id: int, plan_id: str,
                 stripe_customer_id: str | None = None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE x402_users SET plan_id = %s, "
                "stripe_customer_id = COALESCE(%s, stripe_customer_id) "
                "WHERE id = %s",
                (plan_id, stripe_customer_id, user_id),
            )

    # ----- sessions -----

    def create_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM x402_sessions WHERE expires_at < now()")
            conn.execute(
                "INSERT INTO x402_sessions (token_hash, user_id, expires_at) "
                "VALUES (%s, %s, %s)",
                (_token_hash(token), user_id, expires),
            )
        return token

    def get_session_user(self, token: str) -> dict | None:
        if not token:
            return None
        th = _token_hash(token)
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT u.id, u.email, u.plan_id, s.expires_at "
                "FROM x402_sessions s "
                "JOIN x402_users u ON u.id = s.user_id "
                "WHERE s.token_hash = %s",
                (th,),
            ).fetchone()
            if row is None:
                return None
            expires_at = row[3]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                conn.execute(
                    "DELETE FROM x402_sessions WHERE token_hash = %s", (th,)
                )
                return None
        return {"id": row[0], "email": row[1], "plan_id": row[2]}

    def revoke_session(self, token: str) -> None:
        if not token:
            return
        with self._pool.connection() as conn:
            conn.execute(
                "DELETE FROM x402_sessions WHERE token_hash = %s",
                (_token_hash(token),),
            )

    # ----- per-user API keys -----

    def issue_key(self, user_id: int, plan_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO x402_api_keys (token, plan_id, user_id) "
                "VALUES (%s, %s, %s)",
                (token, plan_id, user_id),
            )
        return token

    def list_keys(self, user_id: int) -> list[dict]:
        """Keys owned by the user. ``token`` is included for internal use
        (usage accounting) and must NEVER be rendered in HTML."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT token, plan_id, created_at FROM x402_api_keys "
                "WHERE user_id = %s ORDER BY created_at",
                (user_id,),
            ).fetchall()
        return [
            {
                "kid": kid_for_token(token),
                "plan_id": plan,
                "created_at": created,
                "token": token,
            }
            for token, plan, created in rows
        ]

    def revoke_key_by_kid(self, user_id: int, kid: str) -> bool:
        """Revoke the user's key identified by ``kid``. Keys of other users
        are invisible here (ownership enforced by the WHERE clause)."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT token FROM x402_api_keys WHERE user_id = %s",
                (user_id,),
            ).fetchall()
            for (token,) in rows:
                if kid_for_token(token) == kid:
                    cur = conn.execute(
                        "DELETE FROM x402_api_keys "
                        "WHERE token = %s AND user_id = %s",
                        (token, user_id),
                    )
                    return cur.rowcount > 0
        return False

    def key_owner(self, token: str) -> int | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT user_id FROM x402_api_keys WHERE token = %s",
                (token,),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])

    # ----- Stripe purchase linking -----

    def link_purchase(self, user_id: int, plan_id: str,
                      customer_id: str | None, session_id: str) -> str:
        """Mint the key for a completed checkout tied to an account.

        One transaction: the key (with user_id), the claim row (idempotency
        + one-time view on /success), and the plan upgrade on the user.
        """
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO x402_api_keys "
                "(token, plan_id, customer_id, user_id) "
                "VALUES (%s, %s, %s, %s)",
                (token, plan_id, customer_id, user_id),
            )
            conn.execute(
                "INSERT INTO x402_claims "
                "(session_id, plan_id, api_key, customer_id, issued_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (session_id, plan_id, token, customer_id,
                 datetime.now(timezone.utc)),
            )
            conn.execute(
                "UPDATE x402_users SET plan_id = %s, "
                "stripe_customer_id = COALESCE(%s, stripe_customer_id) "
                "WHERE id = %s",
                (plan_id, customer_id, user_id),
            )
        return token


# ---------------------------------------------------------------------------
# Module-level accessor (mirrors keystore.get_store)
# ---------------------------------------------------------------------------

from api_server.keystore import get_store

_user_stores: dict[int, UserStore] = {}


def get_user_store() -> UserStore | None:
    """UserStore sharing the live Postgres keystore's pool; None in JSON mode.

    Cached per store instance so module reloads in tests get a fresh store.
    """
    store = get_store()
    if getattr(store, "backend", "") != "postgres":
        return None
    pool = getattr(store, "pool", None)
    if pool is None:
        return None
    cached = _user_stores.get(id(store))
    if cached is None:
        cached = UserStore(pool)
        _user_stores[id(store)] = cached
    return cached
