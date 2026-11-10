"""Concurrency ceilings: SSE streams (per user / per run) and estimate slots (per workspace + global).

All state is in-process (per API replica); put per-replica limits low enough that N replicas stay within capacity.
Leases carry a deadline so a leaked lease (client vanished before the response generator started) cannot pin a slot
forever.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager


class SseRegistry:
    def __init__(self, clock=time.monotonic):
        self.clock, self.lock, self.leases = clock, threading.Lock(), {}
        self._n = 0

    def _purge(self, now: float) -> None:
        for k in [k for k, v in self.leases.items() if v[2] < now]:
            self.leases.pop(k, None)

    def acquire(self, user_id: str, run_id: str, max_user: int, max_run: int, ttl: float) -> int | None:
        """Returns a lease id, or None when a cap is reached."""
        now = self.clock()
        with self.lock:
            self._purge(now)
            if sum(1 for v in self.leases.values() if v[0] == user_id) >= max_user:
                return None
            if sum(1 for v in self.leases.values() if v[1] == run_id) >= max_run:
                return None
            self._n += 1
            self.leases[self._n] = (user_id, run_id, now + ttl)
            return self._n

    def release(self, lease: int | None) -> None:
        if lease is not None:
            with self.lock:
                self.leases.pop(lease, None)

    def count(self) -> int:
        with self.lock:
            self._purge(self.clock())
            return len(self.leases)


class SlotLimiter:
    """At most ``per_key`` concurrent holders per key and ``total`` overall; non-blocking."""

    def __init__(self, per_key: int, total: int):
        self.per_key, self.total, self.lock = per_key, total, threading.Lock()
        self.held: dict[str, int] = {}

    def try_acquire(self, key: str) -> bool:
        with self.lock:
            if sum(self.held.values()) >= self.total or self.held.get(key, 0) >= self.per_key:
                return False
            self.held[key] = self.held.get(key, 0) + 1
            return True

    def release(self, key: str) -> None:
        with self.lock:
            n = self.held.get(key, 0) - 1
            if n <= 0:
                self.held.pop(key, None)
            else:
                self.held[key] = n

    @contextmanager
    def slot(self, key: str):
        if not self.try_acquire(key):
            yield False
            return
        try:
            yield True
        finally:
            self.release(key)
