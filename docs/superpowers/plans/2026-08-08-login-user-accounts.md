# Login + User Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-service signup/login (argon2id + Neon-backed sessions), a user dashboard for API keys, and Stripe purchases linked to accounts — anonymous checkout flow unchanged.

**Architecture:** Three new modules — `api_server/pages.py` (shared page chrome extracted from `app.py`), `api_server/auth.py` (passwords, sessions, per-user keys; `UserStore` reuses the live `DBKeyStore` connection pool), `api_server/auth_pages.py` (an `APIRouter` with server-rendered routes included into the existing FastAPI app). The Stripe webhook gains an optional account-link branch keyed on `client_reference_id="user:<id>"`.

**Tech Stack:** FastAPI, psycopg3 (existing pool), argon2-cffi (new), python-multipart (new, required by `fastapi.Form`), Stripe python SDK (existing), pytest + TestClient.

**Spec:** `docs/superpowers/specs/2026-08-08-login-user-accounts-design.md`

## Global Constraints

- **$0 budget.** Never create paid cloud resources. Tests only touch the existing free Neon cluster via `TEST_DATABASE_URL`.
- **Exactly two new dependencies:** `argon2-cffi>=23.1` and `python-multipart>=0.0.9` — add to BOTH `pyproject.toml` (`[project] dependencies`) and `requirements.txt` (Render builds from requirements.txt, which installs this project via the `.` line, so pyproject is authoritative; keep both in sync).
- **Git identity is passed per invocation** (repo git config is intentionally empty):
  `git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "..."`
  Stage files by explicit name only (never `-A`/`.`). **Never push** — the owner pushes.
- **Secrets:** never print secret values. `TEST_DATABASE_URL` enters via secret-shuttle: `secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest ...`. `test.refs` contains only the vault ref, no secret.
- **Environment:** Windows + Git Bash. Python venv at `.venv/Scripts/python.exe` (3.12). pytest config in `pyproject.toml` (`asyncio_mode = "strict"`; tests here are sync TestClient tests).
- **Baseline before starting:** `.venv/Scripts/python.exe -m pytest tests/ -q` → 100 passed / 10 skipped (DB-gated tests skip without `TEST_DATABASE_URL`).
- **UI copy in English** (the existing site is English).
- **Do not touch Stripe product/price configuration.** Only code-level integration.
- Commit after each task (messages provided). All commits ride on `main`; Render auto-deploys on push (owner pushes at the end).

---

## File Structure

| File | Responsibility |
|---|---|
| `api_server/pages.py` (NEW) | Shared page chrome: `PAGE_CSS`, `PAGE_NAV`, `PAGE_FOOTER` (moved verbatim from app.py) + `auth_nav_links(logged_in)` for the landing nav. |
| `api_server/auth.py` (NEW) | Pure primitives (email normalization, argon2id hash/verify, `kid_for_token`) + `UserStore` (users, sessions, per-user keys, Stripe purchase linking) + `get_user_store()`. No FastAPI imports. |
| `api_server/auth_pages.py` (NEW) | `APIRouter`: `/signup`, `/login`, `/logout`, `/dashboard`, `/dashboard/keys`, `/dashboard/keys/revoke`, `/dashboard/upgrade`. Server-rendered HTML. |
| `api_server/dbkeystore.py` (EDIT) | Add public `pool` property (so `UserStore` shares the pool; no second connection pool — Neon free tier connection limits). |
| `api_server/stripe_integration.py` (EDIT) | `create_checkout_session` accepts `client_reference_id` / `customer_email` / `customer`; `retrieve_session` returns `client_reference_id` + `customer_email`. |
| `api_server/app.py` (EDIT) | Import pages chrome from `pages.py`; include auth router; landing nav auth links; webhook account-link branch; `/success` dashboard note for logged-in owners. |
| `tests/test_auth.py` (NEW) | Primitives unit tests + DB-gated `UserStore` integration tests. |
| `tests/test_auth_pages.py` (NEW) | JSON-mode degradation (503) + DB-gated endpoint flows + Stripe extension unit tests. |
| `tests/test_api_server.py` (EDIT) | `TestPagesChrome` (Task 1). |
| `pyproject.toml`, `requirements.txt`, `.gitignore` (EDIT) | Dependencies; ignore `*.refs`. |

---

### Task 1: Dependencies + extract page chrome to `pages.py` + expose DB pool

**Files:**
- Modify: `pyproject.toml` (dependencies list), `requirements.txt`, `.gitignore`
- Create: `api_server/pages.py`
- Modify: `api_server/app.py` (remove chrome constants, import from pages), `api_server/dbkeystore.py` (pool property)
- Test: `tests/test_api_server.py` (add `TestPagesChrome`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `api_server.pages.PAGE_CSS`, `PAGE_NAV`, `PAGE_FOOTER` (str constants), `auth_nav_links(logged_in: bool) -> str`; `DBKeyStore.pool` property. Later tasks import these names exactly.

- [ ] **Step 1: Add dependencies**

`pyproject.toml` — in `[project] dependencies`, after the `psycopg[binary,pool]>=3.2` line add:

```toml
    # Accounts: argon2id password hashing (api_server.auth).
    "argon2-cffi>=23.1",
    # Required by fastapi.Form (signup/login form posts).
    "python-multipart>=0.0.9",
```

`requirements.txt` — append the same two lines at the end:

```
argon2-cffi>=23.1
python-multipart>=0.0.9
```

`.gitignore` — append:

```
# secret-shuttle env-ref files (refs only, never secrets — kept local anyway)
*.refs
```

- [ ] **Step 2: Install into the venv**

Run:
```bash
.venv/Scripts/python.exe -m pip install argon2-cffi python-multipart
```
Expected: `Successfully installed argon2-cffi-... argon2-low-level... python-multipart-...`
(If `No module named pip` appears, run `.venv/Scripts/python.exe -m ensurepip --default-pip` first.)

- [ ] **Step 3: Write the failing tests**

Append to `tests/test_api_server.py` (end of file):

```python
class TestPagesChrome:
    def test_page_nav_has_login_link(self) -> None:
        from api_server.pages import PAGE_NAV
        assert 'href="/login"' in PAGE_NAV

    def test_auth_nav_links(self) -> None:
        from api_server.pages import auth_nav_links
        logged_out = auth_nav_links(False)
        assert "/login" in logged_out and "/signup" in logged_out
        assert auth_nav_links(True) == '<a href="/dashboard">My dashboard</a>'
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_server.py::TestPagesChrome -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api_server.pages'`

- [ ] **Step 5: Create `api_server/pages.py`**

Move the three constants from `api_server/app.py` into this new file **verbatim** (the existing strings in app.py are the source of truth — copy them byte-for-byte; do NOT retype them from the snippets below):
- `PAGE_CSS` = current `_PAGE_CSS` (app.py lines ~1564–1645)
- `PAGE_FOOTER` = current `_PAGE_FOOTER` (app.py lines ~1669–1677)
- `PAGE_NAV` = current `_PAGE_NAV` (app.py lines ~1647–1667) with ONE change: add `<a href="/login">Log in</a>` as the first entry of `nav-right` (before the Contact link).

```python
"""Shared chrome (CSS / nav / footer) for the server-rendered pages.

Lives here (not in app.py) so secondary page modules (auth_pages) can reuse
it without importing the 2000+ line app module (circular imports).
"""

from __future__ import annotations

PAGE_CSS = """<verbatim copy of app.py _PAGE_CSS>"""

PAGE_NAV = """
<nav class="navbar">
  <div class="nav-left">
    <a href="/" style="display:flex;align-items:center;gap:10px;text-decoration:none;">
      <img class="icon" src="/static/logo-mark-512.png" alt="x402 validator" width="28" height="28" style="border-radius:6px;">
      <span style="color:#0a0a0a;font-family:'Instrument Sans',sans-serif;font-weight:700;font-size:15px;letter-spacing:-0.01em;">x402 validator</span>
    </a>
  </div>
  <div class="nav-links">
    <a href="/#audit">Try It Free</a>
    <a href="/#pricing">Pricing</a>
    <a href="/vs-x402-doctor">Compare</a>
    <a href="/open">Open</a>
    <a href="/health">Status</a>
  </div>
  <div class="nav-right">
    <a href="/login">Log in</a>
    <a href="https://github.com/MSSATANASS/x402-validator-tools/issues">Contact</a>
    <a class="btn-primary-pill" href="/create-checkout-session?plan_id=pro">Get Started</a>
  </div>
</nav>
"""

PAGE_FOOTER = """<verbatim copy of app.py _PAGE_FOOTER>"""


def auth_nav_links(logged_in: bool) -> str:
    """Right-side nav links for the landing: dashboard if a session exists,
    log in / sign up otherwise."""
    if logged_in:
        return '<a href="/dashboard">My dashboard</a>'
    return '<a href="/login">Log in</a>\n    <a href="/signup">Sign up</a>'
```

- [ ] **Step 6: Rewire `app.py` to use `pages.py`**

In `api_server/app.py`:

a) After `from api_server.keystore import get_store` (line ~48) add:

```python
from api_server.pages import (
    PAGE_CSS as _PAGE_CSS,
    PAGE_FOOTER as _PAGE_FOOTER,
    PAGE_NAV as _PAGE_NAV,
    auth_nav_links as _auth_nav_links,
)
```

b) Delete the three constant definitions: `_PAGE_CSS = """ ... """` (lines ~1564–1645), `_PAGE_NAV = """ ... """` (lines ~1647–1667), `_PAGE_FOOTER = """ ... """` (lines ~1669–1677). Keep the section comment; replace the deleted block with:

```python
# Shared page chrome moved to api_server.pages (imported at the top of this
# module as _PAGE_CSS / _PAGE_NAV / _PAGE_FOOTER).
```

The `_VS_DOCTOR_HTML` and `_OPEN_HTML` handlers keep using `_PAGE_CSS`/`_PAGE_NAV`/`_PAGE_FOOTER` unchanged — the aliases above satisfy them.

- [ ] **Step 7: Add the `pool` property to `DBKeyStore`**

In `api_server/dbkeystore.py`, after `close()` (line ~130) add:

```python
    @property
    def pool(self):
        """Expose the connection pool so api_server.auth.UserStore can share
        it (one pool per process — Neon free tier limits connections)."""
        return self._pool
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass (102 passed / 10 skipped — baseline 100 + 2 new). No behavior change; `/open` and `/vs-x402-doctor` now show the extra "Log in" nav link.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml requirements.txt .gitignore api_server/pages.py api_server/app.py api_server/dbkeystore.py tests/test_api_server.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "$(cat <<'EOF'
refactor: extract page chrome to pages.py; add auth dependencies

Prep for user accounts: PAGE_CSS/NAV/FOOTER move to api_server.pages so
auth pages can reuse them without circular imports; nav gains a Log in
link. Adds argon2-cffi + python-multipart and DBKeyStore.pool (shared by
the upcoming UserStore). No behavior change.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `auth.py` pure primitives (no DB)

**Files:**
- Create: `api_server/auth.py` (primitives only this task; UserStore comes in Task 3)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `argon2-cffi` (installed Task 1).
- Produces (used by Tasks 3–7): `SESSION_COOKIE = "x402_session"`, `SESSION_TTL_DAYS = 30`, `normalize_email(str) -> str`, `is_valid_email(str) -> bool`, `is_valid_password(str) -> bool`, `hash_password(str) -> str`, `verify_password(stored_hash, password) -> bool`, `kid_for_token(token) -> str` (first 12 hex chars of sha256), `KID_LEN = 12`, `DuplicateEmail(Exception)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auth.py`:

```python
"""Tests for api_server.auth (user accounts).

Layout: TestPrimitives runs anywhere (no DB). TestUserStoreIntegration
(Task 3) runs only with TEST_DATABASE_URL.
"""

from __future__ import annotations

import pytest

from api_server import auth


class TestPrimitives:
    def test_normalize_email(self):
        assert auth.normalize_email("  Foo@Example.COM ") == "foo@example.com"
        assert auth.normalize_email("") == ""

    def test_is_valid_email(self):
        assert auth.is_valid_email("a@b.co")
        assert auth.is_valid_email("user.name+tag@sub.example.org")
        assert not auth.is_valid_email("no-at-sign")
        assert not auth.is_valid_email("a@b")          # no dot in domain
        assert not auth.is_valid_email("a b@c.de")     # space
        assert not auth.is_valid_email("x" * 400)      # too long

    def test_is_valid_password_bounds(self):
        assert not auth.is_valid_password("7chars!!")      # 7 chars
        assert auth.is_valid_password("8chars!!")          # 8 chars
        assert auth.is_valid_password("x" * 200)
        assert not auth.is_valid_password("x" * 201)
        assert not auth.is_valid_password("")

    def test_password_hash_roundtrip(self):
        h = auth.hash_password("correct horse battery")
        assert h != "correct horse battery"
        assert h.startswith("$argon2")
        assert auth.verify_password(h, "correct horse battery") is True
        assert auth.verify_password(h, "wrong password") is False

    def test_verify_password_garbage_hash_is_false(self):
        assert auth.verify_password("not-a-real-hash", "whatever") is False

    def test_kid_for_token(self):
        kid = auth.kid_for_token("tok-abc")
        assert len(kid) == auth.KID_LEN
        assert all(c in "0123456789abcdef" for c in kid)
        assert kid == auth.kid_for_token("tok-abc")      # deterministic
        assert kid != auth.kid_for_token("tok-xyz")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth.py -v`
Expected: collection ERROR / `ModuleNotFoundError: No module named 'api_server.auth'`

- [ ] **Step 3: Implement `api_server/auth.py` (primitives)**

```python
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
from argon2.exceptions import InvalidHashError, VerifyMismatchError

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth.py -v`
Expected: 6 passed. Then full suite: `.venv/Scripts/python.exe -m pytest tests/ -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add api_server/auth.py tests/test_auth.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "$(cat <<'EOF'
feat: auth primitives — argon2id hashing, email rules, key ids

Pure building blocks for user accounts (no DB yet): password
hash/verify, email normalization/validation, kid = sha256(token)[:12]
for safe key references in HTML.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `UserStore` — users, sessions, per-user keys (Neon)

**Files:**
- Modify: `api_server/auth.py` (schema + `UserStore` + `get_user_store`)
- Test: `tests/test_auth.py` (add DB-gated integration tests)
- Create: `test.refs` at repo root (gitignored since Task 1)

**Interfaces:**
- Consumes: `DBKeyStore.pool` (Task 1), primitives from Task 2, keystore schema (tables `x402_api_keys`, `x402_claims` exist because `UserStore` is always built from a live `DBKeyStore`'s pool, whose constructor already ran `ensure_schema`).
- Produces: `UserStore(pool)` with methods `create_user(email, password) -> int` (raises `DuplicateEmail`), `get_user(user_id) -> dict|None` (`id, email, plan_id, stripe_customer_id, created_at`), `authenticate(email, password) -> int|None`, `set_plan(user_id, plan_id, stripe_customer_id=None)`, `create_session(user_id) -> token`, `get_session_user(token) -> dict|None` (`id, email, plan_id`), `revoke_session(token)`, `issue_key(user_id, plan_id) -> token`, `list_keys(user_id) -> [{"kid", "plan_id", "created_at", "token"}]` (token is internal — never render it), `revoke_key_by_kid(user_id, kid) -> bool`, `key_owner(token) -> int|None`, `link_purchase(user_id, plan_id, customer_id, session_id) -> token`; module function `get_user_store() -> UserStore|None` (None when the keystore is JSON-mode).

- [ ] **Step 1: Create `test.refs` at the repo root**

File `test.refs` (contains ONLY a vault ref — no secret):

```
TEST_DATABASE_URL=ss://local/dev/NEON_DATABASE_URL
```

- [ ] **Step 2: Write the failing DB-gated tests**

Append to `tests/test_auth.py` (also add `import hashlib, os, secrets` at the top with the other imports):

```python
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (point it at the Neon test DB)",
)


def _unique_email() -> str:
    return f"u-{secrets.token_hex(6)}@example.com"


@pytest.fixture(scope="module")
def stores():
    """Shared DBKeyStore + UserStore on the Neon test DB (one pool)."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    from api_server.dbkeystore import DBKeyStore

    db = DBKeyStore(TEST_DATABASE_URL)
    us = auth.UserStore(db.pool)
    yield db, us
    db.close()


@needs_db
class TestUserStoreIntegration:
    def test_create_get_authenticate(self, stores):
        _, us = stores
        email = _unique_email()
        uid = us.create_user(email, "password123")
        u = us.get_user(uid)
        assert u is not None
        assert u["email"] == email
        assert u["plan_id"] == "free"
        assert u["stripe_customer_id"] is None
        assert us.authenticate(email, "password123") == uid
        assert us.authenticate(email, "wrong-password") is None
        assert us.authenticate("missing@example.com", "password123") is None

    def test_duplicate_email_raises(self, stores):
        _, us = stores
        email = _unique_email()
        us.create_user(email, "password123")
        with pytest.raises(auth.DuplicateEmail):
            us.create_user(email.upper(), "password456")  # normalized dup

    def test_session_lifecycle(self, stores):
        _, us = stores
        uid = us.create_user(_unique_email(), "password123")
        token = us.create_session(uid)
        user = us.get_session_user(token)
        assert user is not None
        assert user["id"] == uid
        assert user["plan_id"] == "free"
        us.revoke_session(token)
        assert us.get_session_user(token) is None
        assert us.get_session_user("") is None
        assert us.get_session_user("never-a-session") is None

    def test_expired_session_rejected(self, stores):
        db, us = stores
        uid = us.create_user(_unique_email(), "password123")
        token = us.create_session(uid)
        with db.pool.connection() as conn:
            conn.execute(
                "UPDATE x402_sessions SET expires_at = now() - interval '1 day' "
                "WHERE token_hash = %s",
                (hashlib.sha256(token.encode()).hexdigest(),),
            )
        assert us.get_session_user(token) is None

    def test_key_issue_list_revoke(self, stores):
        _, us = stores
        uid = us.create_user(_unique_email(), "password123")
        token = us.issue_key(uid, "pro")
        keys = us.list_keys(uid)
        assert len(keys) == 1
        assert keys[0]["kid"] == auth.kid_for_token(token)
        assert keys[0]["plan_id"] == "pro"
        # another user cannot revoke it
        uid2 = us.create_user(_unique_email(), "password123")
        assert us.revoke_key_by_kid(uid2, keys[0]["kid"]) is False
        # owner can
        assert us.revoke_key_by_kid(uid, keys[0]["kid"]) is True
        assert us.list_keys(uid) == []
        assert us.revoke_key_by_kid(uid, keys[0]["kid"]) is False

    def test_key_owner(self, stores):
        db, us = stores
        uid = us.create_user(_unique_email(), "password123")
        token = us.issue_key(uid, "free")
        assert us.key_owner(token) == uid
        anon = db.issue("free")
        try:
            assert us.key_owner(anon) is None  # anonymous key: no owner
        finally:
            db.revoke(anon)
        assert us.key_owner("never-existed") is None
        us.revoke_key_by_kid(uid, auth.kid_for_token(token))

    def test_link_purchase(self, stores):
        db, us = stores
        uid = us.create_user(_unique_email(), "password123")
        session_id = f"cs_test_{secrets.token_hex(4)}"
        token = us.link_purchase(uid, "pro", "cus_link_test", session_id)
        try:
            u = us.get_user(uid)
            assert u["plan_id"] == "pro"
            assert u["stripe_customer_id"] == "cus_link_test"
            claim = db.claim_by_session(session_id)
            assert claim is not None and claim["api_key"] == token
            assert us.key_owner(token) == uid
            assert db.get(token) == "pro"  # key is live for /validate
        finally:
            db.revoke(token)

    def test_set_plan_keeps_customer_when_none(self, stores):
        _, us = stores
        uid = us.create_user(_unique_email(), "password123")
        us.set_plan(uid, "pro", stripe_customer_id="cus_keep")
        us.set_plan(uid, "enterprise", stripe_customer_id=None)
        u = us.get_user(uid)
        assert u["plan_id"] == "enterprise"
        assert u["stripe_customer_id"] == "cus_keep"  # COALESCE keeps it
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/test_auth.py -q
```
Expected: TestPrimitives pass; TestUserStoreIntegration FAIL with `AttributeError: module 'api_server.auth' has no attribute 'UserStore'`.

- [ ] **Step 4: Implement schema + `UserStore` + `get_user_store`**

Append to `api_server/auth.py`:

```python
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
    "CREATE UNIQUE INDEX IF NOT EXISTS x402_users_email_idx "
    "ON x402_users (lower(email))",
    """
    CREATE TABLE IF NOT EXISTS x402_sessions (
        token_hash TEXT PRIMARY KEY,
        user_id    BIGINT NOT NULL REFERENCES x402_users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    "ALTER TABLE x402_api_keys ADD COLUMN IF NOT EXISTS user_id BIGINT "
    "REFERENCES x402_users(id) ON DELETE SET NULL",
    "CREATE INDEX IF NOT EXISTS x402_api_keys_user_idx "
    "ON x402_api_keys (user_id)",
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

    def get_user(self, user_id: int) -> Optional[dict]:
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

    def authenticate(self, email: str, password: str) -> Optional[int]:
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
                 stripe_customer_id: Optional[str] = None) -> None:
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

    def get_session_user(self, token: str) -> Optional[dict]:
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

    def key_owner(self, token: str) -> Optional[int]:
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
                      customer_id: Optional[str], session_id: str) -> str:
        """Mint the key for a completed checkout tied to an account.

        One transaction: the key (with user_id), the claim row (idempotency
        + one-time view on /success), and the plan upgrade on the user.
        """
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn:
            with conn.transaction():
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

from api_server.keystore import get_store  # noqa: E402  (after classes)

_user_stores: dict[int, "UserStore"] = {}


def get_user_store() -> Optional[UserStore]:
    """UserStore sharing the live Postgres keystore's pool; None in JSON mode.

    Cached per store instance so module reloads in tests get a fresh store.
    """
    store = get_store()
    if getattr(store, "backend", "") != "postgres":
        return None
    cached = _user_stores.get(id(store))
    if cached is None:
        cached = UserStore(store.pool)
        _user_stores[id(store)] = cached
    return cached
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/test_auth.py -q
```
Expected: all primitives + all integration tests pass.

Then confirm the no-DB path still skips cleanly:
`.venv/Scripts/python.exe -m pytest tests/ -q` → integration tests skipped, everything else green.

- [ ] **Step 6: Commit**

```bash
git add api_server/auth.py tests/test_auth.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "$(cat <<'EOF'
feat: UserStore — users, sessions and per-user keys in Neon

Idempotent schema (x402_users, x402_sessions, user_id on x402_api_keys)
plus account CRUD, sha256-only session storage, kid-scoped key revocation
and atomic Stripe purchase linking. Shares the keystore pool; JSON mode
returns None from get_user_store.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

(`test.refs` stays untracked — it is gitignored since Task 1.)

---

### Task 4: Signup / login / logout routes

**Files:**
- Create: `api_server/auth_pages.py` (router skeleton, shell template, helpers, the three auth routes)
- Modify: `api_server/app.py` (include the router)
- Test: `tests/test_auth_pages.py`

**Interfaces:**
- Consumes: `auth.get_user_store()`, `auth.SESSION_COOKIE`, `SESSION_TTL_DAYS`, primitives (Task 2/3); `ratelimit.get_limiter()`; `pages` chrome (Task 1).
- Produces: `auth_pages.router` (APIRouter), `auth_pages.current_user(request) -> dict|None`. Env-tunable limits read at request time: `SIGNUP_DAILY_LIMIT` (default 5), `LOGIN_DAILY_LIMIT` (default 50).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auth_pages.py`:

```python
"""Endpoint tests for auth routes.

JSON-mode tests (no DATABASE_URL) run anywhere: auth routes must degrade
to 503. DB-gated tests need TEST_DATABASE_URL (Neon) and exercise the
real flows.
"""

from __future__ import annotations

import importlib
import os
import re
import secrets
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
needs_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set"
)


@pytest.fixture
def json_client(tmp_path, monkeypatch):
    """App in JSON-keystore mode: auth routes must answer 503."""
    monkeypatch.setenv("API_KEYS_FILE", str(tmp_path / "api_keys.json"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import api_server.keystore  # noqa: F401
    import api_server.app  # noqa: F401
    importlib.reload(sys.modules["api_server.keystore"])
    app_mod = importlib.reload(sys.modules["api_server.app"])
    from api_server import auth as auth_mod, ratelimit as rl_mod
    auth_mod._user_stores.clear()
    rl_mod.reset_limiter()
    return TestClient(app_mod.app)


class TestJsonModeDegradation:
    def test_signup_get_503(self, json_client):
        assert json_client.get("/signup").status_code == 503

    def test_signup_post_503(self, json_client):
        r = json_client.post(
            "/signup", data={"email": "a@b.co", "password": "password123"}
        )
        assert r.status_code == 503

    def test_login_get_503(self, json_client):
        assert json_client.get("/login").status_code == 503

    def test_logout_without_db_still_works(self, json_client):
        r = json_client.post("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"


# ---------------------------------------------------------------------------
# DB-gated flows (Neon)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def db_client():
    """App wired to the Neon test DB (one pool for the whole module)."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    import api_server.keystore  # noqa: F401
    import api_server.app  # noqa: F401
    importlib.reload(sys.modules["api_server.keystore"])
    app_mod = importlib.reload(sys.modules["api_server.app"])
    from api_server import auth as auth_mod, ratelimit as rl_mod
    auth_mod._user_stores.clear()
    rl_mod.reset_limiter()
    yield TestClient(app_mod.app)
    auth_mod._user_stores.clear()
    rl_mod.reset_limiter()
    if saved is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = saved
    importlib.reload(sys.modules["api_server.keystore"])


def _signup(client, email=None, password="password123"):
    email = email or f"u-{secrets.token_hex(6)}@example.com"
    r = client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )
    return email, r


@needs_db
class TestSignupLoginLogout:
    def test_signup_creates_session_and_redirects(self, db_client):
        db_client.cookies.clear()
        email, r = _signup(db_client)
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        assert "x402_session" in db_client.cookies
        dash = db_client.get("/dashboard")
        assert dash.status_code == 200
        assert email in dash.text

    def test_signup_validates_email(self, db_client):
        db_client.cookies.clear()
        r = db_client.post(
            "/signup", data={"email": "not-an-email", "password": "password123"}
        )
        assert r.status_code == 400
        assert "valid email" in r.text

    def test_signup_validates_password(self, db_client):
        db_client.cookies.clear()
        r = db_client.post(
            "/signup",
            data={"email": f"u-{secrets.token_hex(4)}@example.com",
                  "password": "short"},
        )
        assert r.status_code == 400
        assert "8" in r.text

    def test_signup_duplicate_email_409(self, db_client):
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        db_client.cookies.clear()
        r = db_client.post(
            "/signup", data={"email": email.upper(), "password": "password123"}
        )
        assert r.status_code == 409
        assert "already exists" in r.text

    def test_login_wrong_password_generic_error(self, db_client):
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        db_client.cookies.clear()
        r = db_client.post(
            "/login", data={"email": email, "password": "wrong-password"}
        )
        assert r.status_code == 401
        assert "Invalid email or password" in r.text
        assert "x402_session" not in db_client.cookies

    def test_login_unknown_email_same_generic_error(self, db_client):
        db_client.cookies.clear()
        r = db_client.post(
            "/login",
            data={"email": "ghost@example.com", "password": "password123"},
        )
        assert r.status_code == 401
        assert "Invalid email or password" in r.text

    def test_login_success_redirects_to_dashboard(self, db_client):
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        db_client.cookies.clear()
        r = db_client.post(
            "/login", data={"email": email, "password": "password123"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        assert "x402_session" in db_client.cookies

    def test_logout_revokes_session(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.post("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        dash = db_client.get("/dashboard", follow_redirects=False)
        assert dash.status_code == 303
        assert dash.headers["location"] == "/login"

    def test_signup_rate_limited(self, db_client, monkeypatch):
        from api_server import ratelimit
        ratelimit.reset_limiter()
        monkeypatch.setenv("SIGNUP_DAILY_LIMIT", "2")
        db_client.cookies.clear()
        _signup(db_client); db_client.cookies.clear()
        _signup(db_client); db_client.cookies.clear()
        _, r = _signup(db_client)
        assert r.status_code == 429
        ratelimit.reset_limiter()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth_pages.py -q`
Expected: 404s (routes don't exist yet — `/signup` returns 404, not 503).

- [ ] **Step 3: Create `api_server/auth_pages.py`**

```python
"""Server-rendered account routes: signup, login, logout, dashboard.

Follows the landing aesthetic via the shared chrome in api_server.pages.
All mutations require the Neon-backed keystore: without DATABASE_URL the
routes answer 503 (same degradation pattern as the keystore itself).

CSRF: the session cookie is SameSite=Lax, which blocks cross-site form
POSTs from third-party origins.
"""

from __future__ import annotations

import html as _html
import os

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api_server import auth, ratelimit
from api_server.pages import PAGE_CSS, PAGE_FOOTER, PAGE_NAV

router = APIRouter()

SESSION_MAX_AGE = auth.SESSION_TTL_DAYS * 86_400
SIGNUP_DAILY_LIMIT_DEFAULT = 5
LOGIN_DAILY_LIMIT_DEFAULT = 50


# ---------------------------------------------------------------------------
# Page shell + form templates
# ---------------------------------------------------------------------------

_AUTH_CSS = """
.auth-card{max-width:460px;margin:0 auto;background:#fff;border:1px solid var(--glass-border);border-radius:14px;padding:32px;}
.auth-card h1{font-size:1.8rem;margin-bottom:6px;}
.field{margin:14px 0;}
.field label{display:block;font-size:0.85rem;color:var(--fg-70);margin-bottom:6px;}
.field input{width:100%;padding:10px 12px;border:1px solid var(--glass-border);border-radius:8px;font:inherit;background:var(--bg);}
.form-btn{margin-top:18px;width:100%;background:#0a0a0a;color:#fff;border:none;padding:12px;border-radius:999px;font-weight:600;font-size:0.95rem;cursor:pointer;font-family:inherit;}
.form-btn:hover{background:var(--accent-hover);}
.form-error{border:1px solid #fecaca;background:#fef2f2;color:#991b1b;border-radius:8px;padding:10px 12px;font-size:0.88rem;margin:12px 0;}
.form-note{font-size:0.85rem;color:var(--fg-60);margin-top:14px;text-align:center;}
.form-note a{color:var(--accent);}
table.keys{width:100%;border-collapse:collapse;margin:14px 0;}
table.keys th,table.keys td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--glass-border);font-size:0.88rem;vertical-align:middle;}
.mini-btn{background:none;border:1px solid var(--glass-border);border-radius:8px;padding:4px 10px;font-size:0.8rem;cursor:pointer;font-family:inherit;color:var(--fg-70);}
.mini-btn:hover{border-color:#991b1b;color:#991b1b;}
.key-box{background:#0a0a0a;color:#e5e5e5;border-radius:10px;padding:14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;word-break:break-all;margin:14px 0;user-select:all;}
"""

_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · x402 validator</title>
<link rel="icon" type="image/png" href="/static/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com" crossorigin>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&family=Instrument+Serif:ital@0;1&display=swap" rel="stylesheet">
<style>__PAGE_CSS____AUTH_CSS__</style>
</head>
<body>
__PAGE_NAV__
<div class="wrap">
__BODY__
</div>
__PAGE_FOOTER__
</body>
</html>"""

_SIGNUP_FORM = """
<div class="auth-card">
  <h1>Create account</h1>
  <p>Free plan included — upgrade anytime.</p>
  __ERROR__
  <form method="post" action="/signup">
    <div class="field"><label for="email">Email</label>
      <input id="email" name="email" type="email" required value="__EMAIL__"></div>
    <div class="field"><label for="password">Password (min 8 characters)</label>
      <input id="password" name="password" type="password" required></div>
    <button class="form-btn" type="submit">Sign up</button>
  </form>
  <div class="form-note">Already have an account? <a href="/login">Log in</a></div>
</div>"""

_LOGIN_FORM = """
<div class="auth-card">
  <h1>Log in</h1>
  __ERROR__
  <form method="post" action="/login">
    <div class="field"><label for="email">Email</label>
      <input id="email" name="email" type="email" required value="__EMAIL__"></div>
    <div class="field"><label for="password">Password</label>
      <input id="password" name="password" type="password" required></div>
    <button class="form-btn" type="submit">Log in</button>
  </form>
  <div class="form-note">No account yet? <a href="/signup">Sign up</a></div>
</div>"""


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _SHELL.replace("__PAGE_CSS__", PAGE_CSS)
        .replace("__AUTH_CSS__", _AUTH_CSS)
        .replace("__PAGE_NAV__", PAGE_NAV)
        .replace("__PAGE_FOOTER__", PAGE_FOOTER)
        .replace("__TITLE__", _html.escape(title))
        .replace("__BODY__", body),
        status_code=status_code,
    )


def _error_box(message: str) -> str:
    return f'<div class="form-error">{_html.escape(message)}</div>'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client and request.client.host
            else "unknown")


def _cookie_secure(request: Request) -> bool:
    """Secure flag follows the public scheme (Render sends x-forwarded-proto;
    TestClient/local http stays cookie-compatible)."""
    fwd = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    proto = fwd or request.url.scheme
    return proto == "https"


def _attach_session(response, token: str, secure: bool) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE, httponly=True, secure=secure,
        samesite="lax", path="/",
    )


def current_user(request: Request):
    """The session user dict (id/email/plan_id) or None."""
    token = request.cookies.get(auth.SESSION_COOKIE)
    if not token:
        return None
    store = auth.get_user_store()
    if store is None:
        return None
    return store.get_session_user(token)


def _require_store():
    store = auth.get_user_store()
    if store is None:
        raise HTTPException(
            503, "Login requires the database backend (DATABASE_URL is not set)"
        )
    return store


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_form(request: Request) -> HTMLResponse:
    _require_store()
    return _page("Sign up", _SIGNUP_FORM.replace("__ERROR__", "")
                 .replace("__EMAIL__", ""))


@router.post("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup_submit(request: Request,
                        email: str = Form(...), password: str = Form(...)):
    store = _require_store()
    ip = _client_ip(request)
    limit = int(os.environ.get("SIGNUP_DAILY_LIMIT", SIGNUP_DAILY_LIMIT_DEFAULT))
    if not ratelimit.get_limiter().allow(f"signup:{ip}", limit):
        raise HTTPException(
            429, "Too many signups from this IP. Try again tomorrow."
        )
    email_n = auth.normalize_email(email)
    if not auth.is_valid_email(email_n):
        return _bad_signup("Please enter a valid email address.", email)
    if not auth.is_valid_password(password):
        return _bad_signup(
            "Password must be between 8 and 200 characters.", email
        )
    try:
        user_id = store.create_user(email_n, password)
    except auth.DuplicateEmail:
        return _bad_signup(
            "An account with that email already exists.", email, status=409
        )
    token = store.create_session(user_id)
    response = RedirectResponse("/dashboard", status_code=303)
    _attach_session(response, token, _cookie_secure(request))
    return response


def _bad_signup(message: str, email: str, status: int = 400) -> HTMLResponse:
    body = (_SIGNUP_FORM
            .replace("__ERROR__", _error_box(message))
            .replace("__EMAIL__", _html.escape(email or "")))
    return _page("Sign up", body, status_code=status)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_form(request: Request) -> HTMLResponse:
    _require_store()
    return _page("Log in", _LOGIN_FORM.replace("__ERROR__", "")
                 .replace("__EMAIL__", ""))


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_submit(request: Request,
                       email: str = Form(...), password: str = Form(...)):
    store = _require_store()
    ip = _client_ip(request)
    limit = int(os.environ.get("LOGIN_DAILY_LIMIT", LOGIN_DAILY_LIMIT_DEFAULT))
    if not ratelimit.get_limiter().allow(f"login:{ip}", limit):
        raise HTTPException(
            429, "Too many login attempts from this IP. Try again later."
        )
    user_id = store.authenticate(auth.normalize_email(email), password)
    if user_id is None:
        body = (_LOGIN_FORM
                .replace("__ERROR__", _error_box("Invalid email or password."))
                .replace("__EMAIL__", _html.escape(email or "")))
        return _page("Log in", body, status_code=401)
    token = store.create_session(user_id)
    response = RedirectResponse("/dashboard", status_code=303)
    _attach_session(response, token, _cookie_secure(request))
    return response


@router.post("/logout", include_in_schema=False)
async def logout(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        store = auth.get_user_store()
        if store is not None:
            store.revoke_session(token)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response
```

- [ ] **Step 4: Include the router in `app.py`**

In `api_server/app.py`, add to the imports (with the other `api_server` imports near line 45–47):

```python
from api_server import auth_pages
```

and after the admin endpoints block (after `admin_revoke_key`, before the "Success / cancel pages" section comment) add:

```python
# ---------------------------------------------------------------------------
# Account routes (signup / login / dashboard — api_server.auth_pages)
# ---------------------------------------------------------------------------

app.include_router(auth_pages.router)
```

- [ ] **Step 5: Run tests**

No-DB mode: `.venv/Scripts/python.exe -m pytest tests/test_auth_pages.py -q`
Expected: TestJsonModeDegradation all pass; DB-gated classes skipped.

DB mode:
```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/test_auth_pages.py tests/test_auth.py -q
```
Expected: all pass. Then full suite: `.venv/Scripts/python.exe -m pytest tests/ -q` green.

- [ ] **Step 6: Commit**

```bash
git add api_server/auth_pages.py api_server/app.py tests/test_auth_pages.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "$(cat <<'EOF'
feat: signup, login, logout routes with server-side sessions

Server-rendered forms, argon2id credentials, HttpOnly SameSite=Lax
cookie (sha256-only in DB), per-IP rate limits (env-tunable), generic
login errors (no user enumeration). 503 degradation without DATABASE_URL.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Dashboard + key management

**Files:**
- Modify: `api_server/auth_pages.py` (dashboard routes + templates)
- Test: `tests/test_auth_pages.py` (add `TestDashboard`)

**Interfaces:**
- Consumes: `current_user`, `_require_store` (Task 4); `UserStore.list_keys/issue_key/revoke_key_by_kid` (Task 3); `get_store().usage_this_month` from `api_server.keystore`; `PLANS` from models.
- Produces: `GET /dashboard`, `POST /dashboard/keys`, `POST /dashboard/keys/revoke`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth_pages.py`:

```python
KEY_BOX_RE = re.compile(r'id="keyBox">([^<]+)</div>')
```

and add one method inside the existing `TestJsonModeDegradation` class (the `/dashboard` route only exists from this task on):

```python
    def test_dashboard_redirects_to_login_without_session(self, json_client):
        r = json_client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"
```

Then add the DB-gated class:

```python
@needs_db
class TestDashboard:
    def test_dashboard_requires_session(self, db_client):
        db_client.cookies.clear()
        r = db_client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    def test_create_key_shown_once_and_listed_masked(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.post("/dashboard/keys")
        assert r.status_code == 200
        m = KEY_BOX_RE.search(r.text)
        assert m, "key box missing from the new-key page"
        token = m.group(1).strip()
        assert len(token) >= 40
        dash = db_client.get("/dashboard")
        assert token not in dash.text, "raw token leaked into the dashboard"
        from api_server import auth as auth_mod
        assert auth_mod.kid_for_token(token) in dash.text

    def test_new_key_works_for_validate_gate(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.post("/dashboard/keys")
        token = KEY_BOX_RE.search(r.text).group(1).strip()
        from api_server.keystore import get_store
        assert get_store().get(token) == "free"  # signup plan

    def test_revoke_own_key_only(self, db_client):
        from api_server import auth as auth_mod
        from api_server.keystore import get_store
        db_client.cookies.clear()
        email1, _ = _signup(db_client)
        r = db_client.post("/dashboard/keys")
        token = KEY_BOX_RE.search(r.text).group(1).strip()
        kid = auth_mod.kid_for_token(token)

        # a different user cannot revoke it
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.post("/dashboard/keys/revoke", data={"kid": kid})
        assert r.status_code == 404
        assert get_store().get(token) == "free"  # still alive

        # the owner can
        db_client.cookies.clear()
        db_client.post(
            "/login", data={"email": email1, "password": "password123"}
        )
        r = db_client.post(
            "/dashboard/keys/revoke", data={"kid": kid},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert get_store().get(token) is None
        assert kid not in db_client.get("/dashboard").text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_auth_pages.py::TestDashboard -q` (no-DB: skipped). DB check:
```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/test_auth_pages.py::TestDashboard -q
```
Expected: FAIL — `POST /dashboard/keys` returns 404/405 (route missing).

- [ ] **Step 3: Implement dashboard routes**

Append to `api_server/auth_pages.py` (templates first, then routes). Add `stripe_integration`/`get_store`/`PLANS` imports at the top of the file with the other imports:

```python
from api_server import stripe_integration
from api_server.keystore import get_store
from api_server.models import PLANS
```

Templates (append after `_LOGIN_FORM`):

```python
_DASHBOARD_BODY = """
<h1>My dashboard</h1>
<p>Signed in as <strong>__EMAIL__</strong> · plan: <strong>__PLAN__</strong></p>

<h2>API keys</h2>
__KEYS_TABLE__
<form method="post" action="/dashboard/keys">
  <button class="form-btn" style="width:auto;padding:10px 22px;" type="submit">Create new API key</button>
</form>
<p style="font-size:0.85rem;color:var(--fg-60);">Keys use your plan's monthly
quota and are shown in full only once, at creation.</p>

<h2>Plans</h2>
__UPGRADE__

<form method="post" action="/logout" style="margin-top:40px;">
  <button class="mini-btn" type="submit">Log out</button>
</form>
"""

_KEYS_TABLE = """<table class="keys">
<tr><th>Key</th><th>Plan</th><th>Usage this month</th><th>Created</th><th></th></tr>
__ROWS__
</table>"""

_NEW_KEY_BODY = """
<div class="auth-card" style="max-width:560px;">
  <h1>API key created</h1>
  <p>Save this key — it will not be shown again:</p>
  <div class="key-box" id="keyBox">__API_KEY__</div>
  <button class="form-btn" id="copyBtn" type="button">Copy key</button>
  <div class="form-note"><a href="/dashboard">Back to dashboard</a></div>
</div>
<script>
(function(){
  var btn = document.getElementById('copyBtn');
  if(!btn || !navigator.clipboard) return;
  btn.addEventListener('click', function(){
    navigator.clipboard.writeText(document.getElementById('keyBox').innerText).then(function(){
      btn.innerText = 'Copied!';
      setTimeout(function(){ btn.innerText = 'Copy key'; }, 2000);
    }).catch(function(){ btn.innerText = 'Select + copy manually'; });
  });
})();
</script>
"""


def _upgrade_html(current_plan: str) -> str:
    links = []
    for pid in ("pro", "enterprise"):
        if pid == current_plan:
            continue
        p = PLANS[pid]
        price = f"${p.price_cents // 100}/mo"
        links.append(
            f'<a class="btn-primary-pill" style="display:inline-block;'
            f'margin-right:10px;text-decoration:none;" '
            f'href="/dashboard/upgrade?plan_id={pid}">'
            f'Upgrade to {p.name} — {price}</a>'
        )
    if not links:
        return "<p>You are on the top plan — thank you!</p>"
    return "<p>" + "".join(links) + "</p>"
```

Routes (append at the end of the file):

```python
@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request) -> HTMLResponse:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    store = _require_store()
    keys = store.list_keys(user["id"])
    ks = get_store()
    rows = []
    for k in keys:
        plan = PLANS.get(k["plan_id"])
        quota = plan.requests_per_month if plan else "?"
        used = ks.usage_this_month(k["token"])
        rows.append(
            "<tr>"
            f"<td><code>{_html.escape(k['kid'])}…</code></td>"
            f"<td>{_html.escape(k['plan_id'])}</td>"
            f"<td>{used} / {quota}</td>"
            f"<td>{str(k['created_at'])[:10]}</td>"
            "<td><form method=\"post\" action=\"/dashboard/keys/revoke\">"
            f"<input type=\"hidden\" name=\"kid\" value=\"{_html.escape(k['kid'])}\">"
            "<button class=\"mini-btn\" type=\"submit\">Revoke</button>"
            "</form></td>"
            "</tr>"
        )
    keys_table = (_KEYS_TABLE.replace("__ROWS__", "".join(rows))
                  if rows else
                  "<p>No keys yet — create your first one below.</p>")
    plan_label = PLANS[user["plan_id"]].name if user["plan_id"] in PLANS \
        else user["plan_id"]
    body = (_DASHBOARD_BODY
            .replace("__EMAIL__", _html.escape(user["email"]))
            .replace("__PLAN__", _html.escape(plan_label))
            .replace("__KEYS_TABLE__", keys_table)
            .replace("__UPGRADE__", _upgrade_html(user["plan_id"])))
    return _page("My dashboard", body)


@router.post("/dashboard/keys", response_class=HTMLResponse,
             include_in_schema=False)
async def dashboard_create_key(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    store = _require_store()
    token = store.issue_key(user["id"], user["plan_id"])
    body = _NEW_KEY_BODY.replace("__API_KEY__", _html.escape(token))
    return _page("API key created", body)


@router.post("/dashboard/keys/revoke", include_in_schema=False)
async def dashboard_revoke_key(request: Request, kid: str = Form(...)):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    store = _require_store()
    if not store.revoke_key_by_kid(user["id"], kid):
        raise HTTPException(404, "Key not found")
    return RedirectResponse("/dashboard", status_code=303)
```

- [ ] **Step 4: Run tests**

```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/test_auth_pages.py -q
```
Expected: all pass (JSON-mode + DB). Then full suite no-DB: `.venv/Scripts/python.exe -m pytest tests/ -q` green.

- [ ] **Step 5: Commit**

```bash
git add api_server/auth_pages.py tests/test_auth_pages.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "$(cat <<'EOF'
feat: user dashboard — create/revoke API keys, usage and upgrade

Dashboard lists keys masked by kid with per-key usage/quota, mints keys
for the user's plan (token shown once), and revokes strictly by
ownership (foreign kid -> 404).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Stripe — upgrade route + webhook account linking

**Files:**
- Modify: `api_server/stripe_integration.py` (checkout kwargs + retrieve fields)
- Modify: `api_server/auth_pages.py` (`/dashboard/upgrade`)
- Modify: `api_server/app.py` (webhook link branch)
- Test: `tests/test_auth_pages.py` (add `TestStripeCheckoutExtensions`, `TestUpgradeAndWebhook`)

**Interfaces:**
- Consumes: `UserStore.link_purchase/get_user` (Task 3), `current_user` (Task 4), `stripe_integration` (existing).
- Produces: `create_checkout_session(..., client_reference_id=None, customer_email=None, customer=None)`; `retrieve_session` dict gains `client_reference_id`, `customer_email`; webhook response gains `user_id` when linked; `GET /dashboard/upgrade?plan_id=...`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth_pages.py`:

```python
class TestStripeCheckoutExtensions:
    def test_create_checkout_passes_user_link_params(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
        import api_server.stripe_integration as si
        captured = {}

        class FakeSession:
            url = "https://checkout.stripe.com/mock"

        class FakeCheckout:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return FakeSession()

        class FakeStripe:
            checkout = FakeCheckout()

        monkeypatch.setattr(si, "_get_stripe", lambda: FakeStripe())
        url = si.create_checkout_session(
            "pro", success_url="https://x/success", cancel_url="https://x/cancel",
            client_reference_id="user:7", customer_email="a@b.co",
        )
        assert url == "https://checkout.stripe.com/mock"
        assert captured["client_reference_id"] == "user:7"
        assert captured["customer_email"] == "a@b.co"
        assert "customer" not in captured  # never both email and customer

    def test_retrieve_session_includes_link_fields(self, monkeypatch):
        import api_server.stripe_integration as si

        class Sess:
            id = "cs_1"
            customer = "cus_1"
            amount_total = 900
            subscription = None
            mode = "subscription"
            metadata = {"plan_id": "pro"}
            client_reference_id = "user:3"
            customer_email = "a@b.co"

        class FakeCheckout:
            @staticmethod
            def retrieve(sid):
                return Sess()

        class FakeStripe:
            checkout = FakeCheckout()

        monkeypatch.setattr(si, "_get_stripe", lambda: FakeStripe())
        d = si.retrieve_session("cs_1")
        assert d["client_reference_id"] == "user:3"
        assert d["customer_email"] == "a@b.co"


@needs_db
class TestUpgradeAndWebhook:
    def test_upgrade_rejects_bad_plans(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.get("/dashboard/upgrade?plan_id=free",
                          follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        r = db_client.get("/dashboard/upgrade?plan_id=nope",
                          follow_redirects=False)
        assert r.status_code == 400

    def test_upgrade_without_stripe_503(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        with patch("api_server.auth_pages.stripe_integration"
                   ".create_checkout_session", return_value=None):
            r = db_client.get("/dashboard/upgrade?plan_id=pro")
        assert r.status_code == 503

    def test_upgrade_redirects_with_client_reference(self, db_client, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_dummy")
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        captured = {}

        def fake_create(plan_id, *, success_url, cancel_url, **kw):
            captured.update(plan_id=plan_id, **kw)
            return "https://checkout.stripe.com/mock"

        with patch("api_server.stripe_integration.create_checkout_session",
                   fake_create):
            r = db_client.get("/dashboard/upgrade?plan_id=pro",
                              follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "https://checkout.stripe.com/mock"
        assert captured["plan_id"] == "pro"
        assert captured["client_reference_id"].startswith("user:")
        assert captured["customer_email"] == email
        assert captured.get("customer") is None

    def test_webhook_links_purchase_to_user(self, db_client):
        from api_server import auth as auth_mod
        from api_server.keystore import get_store
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        uid = auth_mod.get_user_store().authenticate(email, "password123")
        session_id = f"cs_user_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_linked",
                   "amount_total": None, "subscription": "sub_x",
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": f"user:{uid}",
                   "customer_email": email}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            r = db_client.post("/stripe-webhook", content=b"{}",
                               headers={"stripe-signature": "ok"})
        assert r.status_code == 200
        body = r.json()
        assert body["minted"] is True
        assert body["user_id"] == uid
        u = auth_mod.get_user_store().get_user(uid)
        assert u["plan_id"] == "pro"
        assert u["stripe_customer_id"] == "cus_linked"
        claim = get_store().claim_by_session(session_id)
        assert claim is not None
        assert auth_mod.get_user_store().key_owner(claim["api_key"]) == uid
        # the key shows up on the dashboard (session still valid)
        dash = db_client.get("/dashboard")
        assert dash.status_code == 200
        assert auth_mod.kid_for_token(claim["api_key"]) in dash.text

    def test_webhook_anonymous_flow_unchanged(self, db_client):
        db_client.cookies.clear()
        session_id = f"cs_anon_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_anon",
                   "amount_total": None, "subscription": None,
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": None, "customer_email": None}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            r = db_client.post("/stripe-webhook", content=b"{}",
                               headers={"stripe-signature": "ok"})
        assert r.status_code == 200
        body = r.json()
        assert body["minted"] is True
        assert "user_id" not in body  # anonymous: no linking

    def test_webhook_bad_user_ref_falls_back_to_anonymous(self, db_client):
        db_client.cookies.clear()
        session_id = f"cs_badref_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_bad",
                   "amount_total": None, "subscription": None,
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": "user:99999999",  # no such user
                   "customer_email": None}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            r = db_client.post("/stripe-webhook", content=b"{}",
                               headers={"stripe-signature": "ok"})
        assert r.status_code == 200
        body = r.json()
        assert body["minted"] is True          # purchase never lost
        assert "user_id" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/test_auth_pages.py -q
```
Expected: the new tests FAIL (`create_checkout_session` rejects the new kwargs / `retrieve_session` missing fields / upgrade 404 / webhook has no `user_id`).

- [ ] **Step 3: Extend `stripe_integration.py`**

In `api_server/stripe_integration.py`:

a) Replace the `create_checkout_session` signature and `Session.create` call:

```python
def create_checkout_session(
    plan_id: str,
    *,
    success_url: str,
    cancel_url: str,
    client_reference_id: Optional[str] = None,
    customer_email: Optional[str] = None,
    customer: Optional[str] = None,
) -> Optional[str]:
```

(keep the existing docstring; add one line: *"Optionally links the checkout to a user account via ``client_reference_id`` (``user:<id>``)."*)

and build the Stripe call as:

```python
    extra: dict[str, Any] = {}
    if client_reference_id:
        extra["client_reference_id"] = client_reference_id
    if customer:
        extra["customer"] = customer
    elif customer_email:
        extra["customer_email"] = customer_email  # never both with customer

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"plan_id": plan_id},
        **extra,
    )
    return session.url
```

b) In `retrieve_session`, add two fields to the returned dict:

```python
        "client_reference_id": getattr(sess, "client_reference_id", None),
        "customer_email": getattr(sess, "customer_email", None),
```

- [ ] **Step 4: Add `/dashboard/upgrade` to `auth_pages.py`**

Append to `api_server/auth_pages.py`:

```python
@router.get("/dashboard/upgrade", include_in_schema=False)
async def dashboard_upgrade(request: Request, plan_id: str):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    store = _require_store()
    if plan_id not in PLANS:
        raise HTTPException(400, f"Unknown plan: {plan_id!r}")
    plan = PLANS[plan_id]
    if plan.price_cents == 0:
        # free plan: nothing to buy — the user already has it
        return RedirectResponse("/dashboard", status_code=303)
    base = os.environ.get(
        "PUBLIC_URL", "https://x402-validator-tools.onrender.com"
    )
    full_user = store.get_user(user["id"]) or {}
    stripe_customer = full_user.get("stripe_customer_id") or None
    url = stripe_integration.create_checkout_session(
        plan_id,
        success_url=f"{base}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/cancel",
        client_reference_id=f"user:{user['id']}",
        customer=stripe_customer,
        customer_email=None if stripe_customer else user["email"],
    )
    if url is None:
        raise HTTPException(503, "Stripe is not configured (set STRIPE_SECRET_KEY)")
    return RedirectResponse(url, status_code=303)
```

- [ ] **Step 5: Extend the webhook in `app.py`**

In `api_server/app.py` add `from api_server import auth` to the imports (with the other `api_server` imports).

In `stripe_webhook`, replace the final mint block (currently ~lines 2147–2152):

```python
        customer_id = detail.get("customer")
        token = get_store().issue(
            plan_id, customer_id=customer_id, session_id=session_id
        )
        return {"received": True, "type": event_type, "minted": True,
                "plan_id": plan_id, "session_id": session_id}
```

with:

```python
        customer_id = detail.get("customer")

        # Checkouts started from a logged-in account carry
        # client_reference_id="user:<id>": link the purchase to it so the
        # key appears in the user's dashboard. Never lose a payment on a
        # linking failure — fall back to the anonymous flow.
        linked_user_id = None
        ref = detail.get("client_reference_id") or ""
        if ref.startswith("user:"):
            try:
                candidate = int(ref.split(":", 1)[1])
            except ValueError:
                candidate = None
            if candidate is not None:
                user_store = auth.get_user_store()
                if user_store is not None and user_store.get_user(candidate):
                    try:
                        user_store.link_purchase(
                            candidate, plan_id, customer_id, session_id
                        )
                        linked_user_id = candidate
                    except Exception:
                        linked_user_id = None

        if linked_user_id is None:
            get_store().issue(
                plan_id, customer_id=customer_id, session_id=session_id
            )

        result = {"received": True, "type": event_type, "minted": True,
                  "plan_id": plan_id, "session_id": session_id}
        if linked_user_id is not None:
            result["user_id"] = linked_user_id
        return result
```

- [ ] **Step 6: Run tests**

```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: everything passes, including the pre-existing anonymous webhook tests in `tests/test_api_server.py` (regression check). Then no-DB: `.venv/Scripts/python.exe -m pytest tests/ -q` green (DB tests skipped).

- [ ] **Step 7: Commit**

```bash
git add api_server/stripe_integration.py api_server/auth_pages.py api_server/app.py tests/test_auth_pages.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "$(cat <<'EOF'
feat: Stripe purchases linked to accounts (upgrade + webhook)

/dashboard/upgrade creates a checkout with client_reference_id=user:<id>;
checkout.session.completed links the minted key + plan upgrade to the
account when the ref is present. Bad/unknown refs fall back to the
anonymous flow — a payment is never lost. Anonymous behavior unchanged.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Landing nav (session-aware) + `/success` dashboard note

**Files:**
- Modify: `api_server/app.py` (landing placeholder + handler, success page)
- Test: `tests/test_auth_pages.py` (add `TestLandingNavAndSuccess`)

**Interfaces:**
- Consumes: `pages.auth_nav_links` (Task 1), `auth_pages.current_user` (Task 4), `UserStore.key_owner` (Task 3).
- Produces: landing shows Log in/Sign up (logged out) or My dashboard (logged in); `/success` adds a dashboard note when the visitor owns the claimed key.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth_pages.py`:

```python
@needs_db
class TestLandingNavAndSuccess:
    def test_landing_shows_auth_links_logged_out(self, db_client):
        db_client.cookies.clear()
        r = db_client.get("/")
        assert 'href="/login"' in r.text
        assert 'href="/signup"' in r.text
        assert "My dashboard" not in r.text

    def test_landing_shows_dashboard_link_logged_in(self, db_client):
        db_client.cookies.clear()
        _signup(db_client)
        r = db_client.get("/")
        assert 'href="/dashboard"' in r.text

    def test_success_page_note_for_owner(self, db_client):
        from api_server import auth as auth_mod
        db_client.cookies.clear()
        email, _ = _signup(db_client)
        uid = auth_mod.get_user_store().authenticate(email, "password123")
        session_id = f"cs_note_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_note",
                   "amount_total": None, "subscription": None,
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": f"user:{uid}",
                   "customer_email": email}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            db_client.post("/stripe-webhook", content=b"{}",
                           headers={"stripe-signature": "ok"})
        r = db_client.get(f"/success?session_id={session_id}")
        assert r.status_code == 200
        assert "your dashboard" in r.text

    def test_success_page_no_note_for_strangers(self, db_client):
        db_client.cookies.clear()
        # logged out visitor: no note even with a valid claim
        session_id = f"cs_note2_{secrets.token_hex(4)}"
        event = {"type": "checkout.session.completed",
                 "data": {"object": {"id": session_id}}}
        session = {"id": session_id, "customer": "cus_note2",
                   "amount_total": None, "subscription": None,
                   "mode": "subscription", "metadata": {"plan_id": "pro"},
                   "client_reference_id": None, "customer_email": None}
        with patch("api_server.stripe_integration.verify_webhook",
                   return_value=event), \
             patch("api_server.stripe_integration.retrieve_session",
                   return_value=session):
            db_client.post("/stripe-webhook", content=b"{}",
                           headers={"stripe-signature": "ok"})
        r = db_client.get(f"/success?session_id={session_id}")
        assert r.status_code == 200
        assert "your dashboard" not in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/test_auth_pages.py::TestLandingNavAndSuccess -q
```
Expected: FAIL (landing has no signup link; no `__AUTH_NAV__` substitution; no note).

- [ ] **Step 3: Implement the landing nav**

a) In `api_server/app.py`, inside the landing HTML `_LANDING_HTML`, in the `nav-right` block (~lines 1096–1099), insert the placeholder before Contact:

```html
  <div class="nav-right">
    __AUTH_NAV__
    <a class="book-demo" href="https://github.com/MSSATANASS/x402-validator-tools/issues">Contact</a>
    <a class="btn-primary-pill" href="/create-checkout-session?plan_id=pro">Get Started</a>
  </div>
```

b) Replace the landing handler (currently ~lines 1870–1872):

```python
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing() -> HTMLResponse:
    return HTMLResponse(_LANDING_HTML)
```

with:

```python
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing(request: Request) -> HTMLResponse:
    logged_in = False
    try:
        logged_in = auth_pages.current_user(request) is not None
    except Exception:
        logged_in = False  # the landing must never break on auth issues
    return HTMLResponse(
        _LANDING_HTML.replace("__AUTH_NAV__", _auth_nav_links(logged_in))
    )
```

- [ ] **Step 4: Implement the `/success` dashboard note**

a) In `_SUCCESS_WITH_KEY_HTML` (app.py ~line 2266), after the copy button add the placeholder:

```html
  <button class="copy-btn" id="copyBtn" type="button">Copy key</button>
  __OWNER_NOTE__
```

b) Change `_success_html` (~line 2290): keep its existing body/escaping exactly as-is and make only two additions — a new trailing parameter `owner_note: str = ""` and one extra `.replace("__OWNER_NOTE__", owner_note)` at the end of its replacement chain.

c) Replace `success_page` (~lines 2300–2309):

```python
@app.get("/success", response_class=HTMLResponse, include_in_schema=False)
async def success_page(request: Request,
                       session_id: Optional[str] = None) -> HTMLResponse:
    """Display a one-time key view when ``session_id`` is valid, fall back otherwise."""
    if session_id:
        claim = get_store().claim_by_session(session_id)
        if claim and get_store().get(claim["api_key"]) is not None:
            note = ""
            try:
                user = auth_pages.current_user(request)
                user_store = auth.get_user_store()
                if user and user_store and \
                        user_store.key_owner(claim["api_key"]) == user["id"]:
                    note = ('<p style="color:var(--fg-70);font-size:13px;'
                            'margin-top:16px;">This key is also listed in '
                            '<a href="/dashboard">your dashboard</a>.</p>')
            except Exception:
                note = ""  # never break the key view
            html = _success_html(claim["api_key"], claim["plan_id"],
                                 session_id, note)
            get_store().mark_claimed(session_id)
            return html
    return HTMLResponse(_SUCCESS_FALLBACK_HTML)
```

- [ ] **Step 5: Run tests**

```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: all green. Then no-DB full suite: `.venv/Scripts/python.exe -m pytest tests/ -q` green.

- [ ] **Step 6: Commit**

```bash
git add api_server/app.py tests/test_auth_pages.py
git -c user.name="Gael Leonardo Chulim Gongora" -c user.email="mss_ali@users.noreply.github.com" commit -m "$(cat <<'EOF'
feat: session-aware landing nav + dashboard note on /success

Landing shows Log in/Sign up, or My dashboard when a valid session
cookie exists. /success tells logged-in owners their key is in the
dashboard. Auth errors can never break the landing or the key view.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Final verification (no new code)

**Files:** none modified.

- [ ] **Step 1: Full suite without secrets**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass; only `TEST_DATABASE_URL`-gated tests skipped.

- [ ] **Step 2: Full suite against Neon**

Run:
```bash
secret-shuttle run --env-file=test.refs -- .venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: all pass, 0 skipped from the auth tests (other pre-existing skips may remain, e.g. real-Qwen integration tests without `DASHSCOPE_API_KEY`).

- [ ] **Step 3: Local smoke test (optional, manual)**

```bash
secret-shuttle run --env-file=smoke.refs -- .venv/Scripts/python.exe -m uvicorn api_server.app:app --port 8000
```
(`smoke.refs` = `DATABASE_URL=ss://local/dev/NEON_DATABASE_URL` — create it locally if you want to run this; it is gitignored.) Then in a browser: `/signup` → create account → dashboard → create key → copy → use it in a `POST /validate` with `X-API-Key` → revoke it → confirm 401 afterwards. Log out, log back in.

- [ ] **Step 4: Handoff checklist for the owner**

Report to the owner:
1. Commits ready on `main` (Tasks 1–7); push when ready — Render auto-deploys; the schema is idempotent and safe to apply live (existing keys keep `user_id NULL`).
2. No new Render env vars needed (`PUBLIC_URL` already set; cookie `Secure` flag follows `x-forwarded-proto`).
3. Post-deploy verification: sign up on the live site, mint a key, `GET /create-checkout-session?plan_id=free` still redirects to `/success`, and `/validate` with the new key returns 200.
4. Reminders: leaked-credentials revocation (task #1) and Dependabot alerts (2 high) are still open.

---

## Self-Review Notes (already applied)

- Spec coverage: schema ✔ (T3), auth module ✔ (T2/T3), routes ✔ (T4/T5), Stripe linking ✔ (T6), landing nav ✔ (T7), success note ✔ (T7), 503 degradation ✔ (T4), rate limits ✔ (T4), anonymous-flow regression ✔ (T6), masked keys/kid ✔ (T5).
- No placeholders: every step ships real code or exact commands.
- Name consistency: `current_user`, `get_user_store`, `kid_for_token`, `link_purchase`, `SESSION_COOKIE`, `client_reference_id="user:<id>"` used identically across tasks.
- Risk notes: (a) FastAPI `Form` requires python-multipart → added in Task 1 before any route uses it; (b) TestClient cookies respect the `Secure` flag, so `_cookie_secure` keys off `x-forwarded-proto`/scheme (Render sends the header; tests stay http); (c) one shared DB pool (no Neon connection-limit risk); (d) `list_keys` returns raw tokens internally — never rendered, asserted in `test_create_key_shown_once_and_listed_masked`.
