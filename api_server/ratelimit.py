"""In-memory IP rate limiter for public-facing endpoints.

Sliding 24-hour window keyed by client IP. Good enough for a single-replica
deployment; for multi-replica swap for Redis / Memcached.
"""

from __future__ import annotations

import threading
import time as _time
from collections import defaultdict, deque
from collections.abc import Callable

_WINDOW_SECONDS = 86_400  # 24h sliding window


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


def get_limiter() -> IpRateLimiter:
    """Return the module-level default IpRateLimiter."""
    return _limiter


def reset_limiter() -> None:
    """Reset all buckets (used by tests)."""
    _limiter.reset()
