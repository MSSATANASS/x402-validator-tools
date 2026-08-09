"""In-memory rate limiters for public and authenticated endpoints.

- IP limiter: sliding window for demo / signup / login (24h default).
- API-key limiter: per-key hourly caps by plan (opt-in via env).

Single-replica only; for multi-replica swap for Redis / Memcached.
"""

from __future__ import annotations

import os
import threading
import time as _time
from collections import defaultdict, deque
from collections.abc import Callable

_WINDOW_SECONDS = 86_400  # 24h sliding window

# Per-key defaults (requests per window). Override with RATE_LIMIT_* env.
_DEFAULT_KEY_WINDOW = 3_600  # 1 hour
_DEFAULT_KEY_LIMITS: dict[str, int] = {
    "free": 30,
    "pro": 120,
    "enterprise": 600,
}


class IpRateLimiter:
    def __init__(
        self,
        window_seconds: int = _WINDOW_SECONDS,
        *,
        time_func: Callable[[], float] = _time.time,
    ) -> None:
        self._lock = threading.Lock()
        self._window = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._now = time_func

    def _trim(self, bucket: deque[float], now: float) -> None:
        cutoff = now - self._window
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    def allow(self, key: str, limit: int) -> bool:
        """Returns True if the request is within ``limit`` for ``key`` in the
        last ``window_seconds``; otherwise registers an attempt and returns
        False (so the next attempt counts toward the limit too)."""
        now = self._now()
        with self._lock:
            bucket = self._buckets[key]
            self._trim(bucket, now)
            if len(bucket) >= limit:
                # still bump so subsequent attempts see the same state quickly
                bucket.append(now)
                return False
            bucket.append(now)
            return True

    def remaining(self, key: str, limit: int) -> int:
        """How many more requests ``key`` can still make inside the window."""
        now = self._now()
        with self._lock:
            bucket = self._buckets[key]
            self._trim(bucket, now)
            return max(0, limit - len(bucket))

    def reset(self, key: str | None = None) -> None:
        """Test/operator hook. Clears one bucket, or all."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


_limiter = IpRateLimiter()
_key_limiter = IpRateLimiter(
    window_seconds=int(
        os.environ.get("RATE_LIMIT_KEY_WINDOW_SECONDS", str(_DEFAULT_KEY_WINDOW))
    )
)


def get_limiter() -> IpRateLimiter:
    """Return the module-level default IpRateLimiter (IP / public)."""
    return _limiter


def get_key_limiter() -> IpRateLimiter:
    """Return the per-API-key rate limiter."""
    return _key_limiter


def reset_limiter() -> None:
    """Reset all buckets (used by tests)."""
    _limiter.reset()
    _key_limiter.reset()


def key_rate_limit_enabled() -> bool:
    return os.environ.get("API_KEY_RATE_LIMIT_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def key_limit_for_plan(plan_id: str | None) -> int:
    """Max requests per window for an API key on ``plan_id``."""
    pid = (plan_id or "free").lower()
    env_key = f"RATE_LIMIT_KEY_{pid.upper()}"
    raw = os.environ.get(env_key, "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return _DEFAULT_KEY_LIMITS.get(pid, _DEFAULT_KEY_LIMITS["free"])


def allow_api_key(api_key: str, plan_id: str | None) -> bool:
    """True if the key is within its plan's rate limit (or limiting is off)."""
    if not key_rate_limit_enabled():
        return True
    limit = key_limit_for_plan(plan_id)
    # Hash-ish key prefix to avoid storing full secrets in memory maps longer than needed
    bucket_key = f"key:{(api_key or '')[:24]}"
    return _key_limiter.allow(bucket_key, limit)


def remaining_api_key(api_key: str, plan_id: str | None) -> int:
    if not key_rate_limit_enabled():
        return key_limit_for_plan(plan_id)
    bucket_key = f"key:{(api_key or '')[:24]}"
    return _key_limiter.remaining(bucket_key, key_limit_for_plan(plan_id))
