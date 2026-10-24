"""Minimal in-process fixed-window rate limiter.

Keyed by an arbitrary string (we use the authenticated user_ref). This is a
single-instance limiter — adequate for the current deployment and a real abuse
guard, but for multi-instance horizontal scaling it should be backed by Redis or
the provider's own limits. Documented as such in the README.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """Return True if the action is allowed, recording the hit. False if the
        key has exceeded ``limit`` within the trailing ``window_seconds``."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            hits = [t for t in self._hits[key] if t >= cutoff]
            if len(hits) >= limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


# Shared limiters (process-wide).
sos_limiter = RateLimiter()
contacts_limiter = RateLimiter()
