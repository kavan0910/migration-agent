"""In-process token bucket rate limiter.

Per-process only: with N API replicas the effective limit is N times higher. Good enough as a first line of defence
(credential stuffing, accidental loops); put a shared limiter at the edge (reverse proxy / WAF) for real abuse control.
Keys: client IP for unauthenticated/auth routes, user id for authenticated routes, normalised e-mail for the
per-account login throttle (exponential backoff).

Pluggable: ``Limiter`` and ``AttemptStore`` are the interfaces a shared backend (Redis, the database) would implement
so limits hold across replicas. Only the in-memory implementations ship; ``make_limiter`` / ``make_attempt_store``
are the single construction points to swap. (Interface only; no shared store is implemented here.)
"""
from __future__ import annotations

import threading
import time
from typing import Protocol


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


class Limiter(Protocol):
    def allow(self, key: str) -> tuple[bool, float]: ...


class AttemptStore(Protocol):
    """Failure counters with expiry; the only state the per-account login throttle needs."""
    def get(self, key: str) -> tuple[int, float]: ...      # (failures, locked_until_epoch)
    def set(self, key: str, failures: int, locked_until: float, ttl: float) -> None: ...
    def clear(self, key: str) -> None: ...


class MemoryAttemptStore:
    def __init__(self, clock=time.time):
        self.clock, self.d, self.lock = clock, {}, threading.Lock()

    def get(self, key: str) -> tuple[int, float]:
        with self.lock:
            f, locked, exp = self.d.get(key, (0, 0.0, 0.0))
            if exp and exp < self.clock():
                self.d.pop(key, None)
                return 0, 0.0
            return f, locked

    def set(self, key: str, failures: int, locked_until: float, ttl: float) -> None:
        with self.lock:
            self.d[key] = (failures, locked_until, self.clock() + ttl)
            if len(self.d) > 50_000:
                now = self.clock()
                for k in [k for k, v in self.d.items() if v[2] < now]:
                    self.d.pop(k, None)

    def clear(self, key: str) -> None:
        with self.lock:
            self.d.pop(key, None)


class LoginThrottle:
    """Per-account throttle: after ``threshold`` consecutive failures the account is locked for base*2^n seconds
    (capped). Applies equally to unknown e-mails, so it is not an account-existence oracle. A locked account rejects
    even the correct password until the lock expires (otherwise the lock would not slow guessing).
    Trade-off: an attacker can keep a victim's login locked for up to ``max_s``; the per-IP bucket and the audit trail
    are the compensating controls."""

    def __init__(self, store: AttemptStore, threshold: int, base_s: float, max_s: float, clock=time.time):
        self.store, self.threshold, self.base, self.max, self.clock = store, threshold, base_s, max_s, clock

    @staticmethod
    def key(email: str) -> str:
        import hashlib
        return "login:" + hashlib.sha256(email.strip().lower().encode()).hexdigest()

    def retry_after(self, email: str) -> float:
        _, locked = self.store.get(self.key(email))
        return max(0.0, locked - self.clock())

    def failure(self, email: str) -> None:
        k = self.key(email)
        n, _ = self.store.get(k)
        n += 1
        delay = 0.0 if n < self.threshold else min(self.max, self.base * (2 ** (n - self.threshold)))
        self.store.set(k, n, self.clock() + delay, ttl=max(self.max * 2, 3600.0))

    def success(self, email: str) -> None:
        self.store.clear(self.key(email))


def make_limiter(per_minute: int, burst: int | None = None) -> Limiter:
    return TokenBucket(per_minute, burst)


def make_attempt_store() -> AttemptStore:
    return MemoryAttemptStore()
