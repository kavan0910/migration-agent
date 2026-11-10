"""In-process token bucket rate limiter.

Per-process only: with N API replicas the effective limit is N times higher. Good enough as a first line of defence
(credential stuffing, accidental loops); put a shared limiter at the edge (reverse proxy / WAF) for real abuse control.
Keys: client IP for unauthenticated/auth routes, user id for authenticated routes.
"""
from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, per_minute: int, burst: int | None = None, clock=time.monotonic):
        self.rate = per_minute / 60.0
        self.capacity = float(burst or per_minute)
        self.clock = clock
        self.state: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = self.clock()
        with self.lock:
            tokens, last = self.state.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self.state[key] = (tokens - 1.0, now)
                if len(self.state) > 50_000:
                    self._gc(now)
                return True, 0.0
            self.state[key] = (tokens, now)
            return False, (1.0 - tokens) / self.rate if self.rate else 60.0

    def _gc(self, now: float) -> None:
        full_after = self.capacity / self.rate if self.rate else 60.0
        for k in [k for k, (_, last) in self.state.items() if now - last > full_after]:
            self.state.pop(k, None)
