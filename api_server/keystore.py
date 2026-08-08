"""Persistent storage for API keys + per-session claims.

On-disk shape (JSON)::

    {
      "keys":   { "<token>": "<plan_id>", ... },
      "claims": { "<checkout_session_id>": {
                     "plan_id":     "pro",
                     "api_key":     "<token>",
                     "customer_id": "cus_...",
                     "issued_at":   "2026-07-27T12:00:00+00:00",
                     "claimed_at":  null
                  }, ... }
    }

Legacy flat shape ``{"<token>": "<plan_id>", ...}`` is auto-migrated on
next load. Atomic writes (tmp + rename) keep the file crash-safe.

Backend selection happens in ``get_store()``: when ``DATABASE_URL`` is set
the PostgreSQL/PolarDB-backed ``api_server.dbkeystore.DBKeyStore`` is used
(same interface, plus real monthly-quota enforcement and an audit log);
otherwise this JSON store is used. To migrate existing data run
``scripts/migrate_keystore_to_db.py``.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Optional, Union


_LOCK = RLock()
_DEFAULT_PATH = Path(os.environ.get("API_KEYS_FILE", "api_keys.json"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(raw: Union[dict, Any]) -> dict:
    """Normalize any on-disk shape into the wrapped ``{"keys", "claims"}`` shape.

    Accepts the legacy ``{token: plan_id_str}`` flat format too.
    """
    if not isinstance(raw, dict):
        return {"keys": {}, "claims": {}}

    looks_legacy = "keys" not in raw and "claims" not in raw and all(
        isinstance(v, str) for v in raw.values()
    )
    if looks_legacy:
        return {"keys": {str(k): str(v) for k, v in raw.items()}, "claims": {}}

    return {
        "keys": {
            str(k): str(v)
            for k, v in raw.get("keys", {}).items()
            if isinstance(v, str)
        },
        "claims": {
            str(k): v
            for k, v in raw.get("claims", {}).items()
            if isinstance(v, dict)
        },
    }


def _load(path: Path = _DEFAULT_PATH) -> dict:
    """Read the keys file. Migrates legacy flat shape on read."""
    if not path.exists():
        return {"keys": {}, "claims": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"keys": {}, "claims": {}}
    return _migrate(raw)


def _save(data: dict, path: Path = _DEFAULT_PATH) -> None:
    """Atomically write the keys file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class KeyStore:
    """Thread-safe, JSON-backed key+claims store."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        with _LOCK:
            self._data = _load(self._path)

    @property
    def path(self) -> Path:
        return self._path

    # ----- key lookup (used by validation gate) -----

    def __contains__(self, key: str) -> bool:
        return key in self._data["keys"]

    def __getitem__(self, key: str) -> str:
        return self._data["keys"][key]

    def get(self, key: str) -> Optional[str]:
        return self._data["keys"].get(key)

    def all(self) -> dict[str, str]:
        """Flat ``{token: plan_id}`` view for the admin endpoint."""
        return dict(self._data["keys"])

    # ----- issuance -----

    def issue(
        self,
        plan_id: str,
        *,
        customer_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Mint a new random API key for ``plan_id``; persist it; return the key.

        If ``session_id`` is provided, also persist a claim so ``/success`` can
        look the key up by ``session_id``.
        """
        token = secrets.token_urlsafe(32)
        with _LOCK:
            self._data["keys"][token] = plan_id
            if session_id:
                self._data["claims"][session_id] = {
                    "plan_id": plan_id,
                    "api_key": token,
                    "customer_id": customer_id,
                    "issued_at": _now(),
                    "claimed_at": None,
                }
            _save(self._data, self._path)
        return token

    def revoke(self, key: str) -> bool:
        """Remove a key; drops any claims pointing at the key. Returns True if it existed."""
        with _LOCK:
            if key not in self._data["keys"]:
                return False
            del self._data["keys"][key]
            self._data["claims"] = {
                sid: c
                for sid, c in self._data["claims"].items()
                if c.get("api_key") != key
            }
            _save(self._data, self._path)
        return True

    # ----- claim lookup (used by /success) -----

    def claim_by_session(self, session_id: Optional[str]) -> Optional[dict[str, Any]]:
        """Return the persisted claim for ``session_id`` (or ``None``)."""
        if not session_id:
            return None
        return self._data["claims"].get(session_id)

    def mark_claimed(self, session_id: str) -> bool:
        """Stamp ``claimed_at`` on the claim. Returns True if it existed."""
        with _LOCK:
            claim = self._data["claims"].get(session_id)
            if not claim:
                return False
            claim["claimed_at"] = _now()
            _save(self._data, self._path)
            return True

    def claims_all(self) -> dict[str, dict[str, Any]]:
        """Operator view of every persisted claim (debug / admin)."""
        return dict(self._data["claims"])

    # ----- usage accounting (no-ops on the JSON backend) -----
    # The JSON store tracks no per-key usage, so quotas are not enforced
    # (historical behavior). These keep app code store-agnostic; the
    # PostgreSQL-backed DBKeyStore implements real enforcement.

    backend = "json"

    def usage_this_month(self, key: str) -> int:
        return 0

    def quota_allows(self, key: str, plan_id: Optional[str]) -> bool:
        return True

    def record_audit(self, **kwargs: Any) -> None:
        return None


# Default instance — created lazily on first use so test/env changes to
# DATABASE_URL / API_KEYS_FILE are picked up correctly.
_store = None


def _make_default_store() -> "KeyStore":
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        from api_server.dbkeystore import DBKeyStore  # lazy import
        return DBKeyStore(db_url)
    return KeyStore()


def get_store() -> "KeyStore":
    """Return the module-level default store (JSON or PostgreSQL-backed)."""
    global _store
    if _store is None:
        _store = _make_default_store()
    return _store


def reset_store(path: Optional[Path] = None) -> KeyStore:
    """Replace the default store with a fresh JSON one (tests, etc.)."""
    global _store
    _store = KeyStore(path)
    return _store


def reset_default_store() -> None:
    """Drop the cached store; the next get_store() re-reads the environment."""
    global _store
    _store = None
