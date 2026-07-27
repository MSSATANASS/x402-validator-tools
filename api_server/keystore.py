"""Persistent storage for API keys.

Each key → plan_id mapping lives in a JSON file on disk (Render's working
directory by default, /opt/render/project/src/api_keys.json in practice).
The file is read on import and rewritten on every mutation.

This is intentionally simple; for production multi-replica deployments
swap this for PostgreSQL / Redis. The interface stays the same.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from threading import RLock
from typing import Optional


_LOCK = RLock()
_DEFAULT_PATH = Path(os.environ.get("API_KEYS_FILE", "api_keys.json"))


def _load(path: Path = _DEFAULT_PATH) -> dict[str, str]:
    """Read the keys file. Returns empty dict if missing."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, str], path: Path = _DEFAULT_PATH) -> None:
    """Atomically write the keys file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


class KeyStore:
    """Thread-safe, JSON-backed key → plan store."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH
        with _LOCK:
            self._data = _load(self._path)

    @property
    def path(self) -> Path:
        return self._path

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __getitem__(self, key: str) -> str:
        return self._data[key]

    def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    def all(self) -> dict[str, str]:
        return dict(self._data)

    def issue(self, plan_id: str) -> str:
        """Mint a new random API key for the given plan; persist it; return the key."""
        token = secrets.token_urlsafe(32)
        with _LOCK:
            self._data[token] = plan_id
            _save(self._data, self._path)
        return token

    def revoke(self, key: str) -> bool:
        """Remove a key. Returns True if it existed."""
        with _LOCK:
            if key not in self._data:
                return False
            del self._data[key]
            _save(self._data, self._path)
        return True


# Default in-memory instance — the FastAPI app talks to this.
_store = KeyStore()


def get_store() -> KeyStore:
    """Return the module-level default KeyStore."""
    return _store


def reset_store(path: Optional[Path] = None) -> KeyStore:
    """Replace the default store with a fresh one (tests, etc.)."""
    global _store
    _store = KeyStore(path)
    return _store
