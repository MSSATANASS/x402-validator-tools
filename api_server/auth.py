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
from typing import Optional

from argon2 import PasswordHasher

SESSION_COOKIE = "x402_session"
SESSION_TTL_DAYS = 30
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 200
KID_LEN = 12

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hasher = PasswordHasher()
_dummy_hash: Optional[str] = None  # lazy; equalizes login timing


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
