"""In-process TTL cache for audit responses.

**Off by default** (``AUDIT_CACHE_TTL_SECONDS=0``). Enable carefully:

- 402 challenge payloads (esp. ``batch-settlement``) can be dynamic.
- We **never** cache when ``advise``/``explain`` is set.
- We **skip store** when any check is ``batch_settlement_requirements``
  with ``details.applicable is True`` (live channel terms).
- Cache key is ``sha256(url|mode)`` after URL normalization (strip trailing /).

Header ``X-Audit-Cache: HIT|MISS|SKIP|STORE`` is set by the route layer.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from api_server.metrics import record_cache


def cache_ttl_seconds() -> float:
    raw = os.environ.get("AUDIT_CACHE_TTL_SECONDS", "0").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def cache_enabled() -> bool:
    return cache_ttl_seconds() > 0


def normalize_cache_url(url: str) -> str:
    u = (url or "").strip()
    if len(u) > 1 and u.endswith("/"):
        u = u.rstrip("/")
    return u


def make_cache_key(url: str, mode: str) -> str:
    raw = f"{normalize_cache_url(url)}|{mode}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def should_skip_cache_request(*, advise: bool, explain: bool) -> bool:
    return bool(advise or explain)


def should_skip_cache_store(checks: list[Any]) -> bool:
    """Do not store responses that include live batch-settlement offers."""
    for c in checks:
        if isinstance(c, dict):
            name = c.get("name") or c.get("check_name")
            details = c.get("details") or {}
            status = c.get("status")
        else:
            name = getattr(c, "name", None) or getattr(c, "check_name", None)
            details = getattr(c, "details", None) or {}
            status = getattr(c, "status", None)
        if name != "batch_settlement_requirements":
            continue
        if isinstance(details, dict) and details.get("applicable") is True:
            return True
        # FAIL on batch-settlement is still merchant-specific; caching is OK
        # only for N/A (applicable false). Status alone is not enough.
        _ = status
    return False


@dataclass
class _Entry:
    expires_at: float
    payload: dict[str, Any]


class AuditResponseCache:
    def __init__(
        self,
        *,
        time_func: Callable[[], float] = time.time,
        max_entries: int = 512,
    ) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, _Entry] = {}
        self._now = time_func
        self._max = max_entries

    def get(self, key: str) -> dict[str, Any] | None:
        if not cache_enabled():
            return None
        now = self._now()
        with self._lock:
            ent = self._data.get(key)
            if ent is None:
                record_cache("miss")
                return None
            if ent.expires_at <= now:
                self._data.pop(key, None)
                record_cache("miss")
                return None
            record_cache("hit")
            # Return a shallow copy so callers cannot mutate the store
            return dict(ent.payload)

    def set(self, key: str, payload: dict[str, Any], ttl: float | None = None) -> None:
        if not cache_enabled():
            return
        ttl_s = cache_ttl_seconds() if ttl is None else ttl
        if ttl_s <= 0:
            return
        now = self._now()
        with self._lock:
            if len(self._data) >= self._max:
                # Drop expired first, then oldest by expiry
                expired = [k for k, v in self._data.items() if v.expires_at <= now]
                for k in expired:
                    self._data.pop(k, None)
                if len(self._data) >= self._max:
                    oldest = min(self._data.items(), key=lambda kv: kv[1].expires_at)
                    self._data.pop(oldest[0], None)
            self._data[key] = _Entry(expires_at=now + ttl_s, payload=dict(payload))
            record_cache("store")

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = AuditResponseCache()


def get_audit_cache() -> AuditResponseCache:
    return _cache


def reset_audit_cache() -> None:
    _cache.clear()
