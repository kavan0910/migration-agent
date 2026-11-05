"""Append-only JSONL audit log (A-07).

Each record is one line written with a single ``os.write`` on an ``O_APPEND``
descriptor; an advisory ``flock`` makes the seq number monotonic across processes.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _last_seq(fd: int) -> int:
    size = os.fstat(fd).st_size
    if size == 0:
        return 0
    chunk = 8192
    while True:
        start = max(0, size - chunk)
        data = os.pread(fd, size - start, start)
        lines = [ln for ln in data.split(b"\n") if ln.strip()]
        # drop a possibly truncated first line unless we read from file start
        if start > 0 and len(lines) <= 1:
            chunk *= 4
            continue
        for ln in reversed(lines):
            try:
                return int(json.loads(ln)["seq"])
            except (ValueError, KeyError, TypeError):
                continue
        if start == 0:
            return 0
        chunk *= 4


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(
        self,
        agent: str,
        action: str,
        *,
        command: str | None = None,
        duration_ms: float | None = None,
        tool_calls: list[Any] | int | None = None,
        model: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        prompt_version: str | None = None,
        input_hash: str | None = None,
        trace_ref: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "seq": 0,
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "action": action,
            "command": command,
            "duration_ms": duration_ms,
            "tool_calls": tool_calls,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "prompt_version": prompt_version,
            "input_hash": input_hash,
            "trace_ref": trace_ref,
        }
        entry.update(extra)
        with self._lock:
            fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    entry["seq"] = _last_seq(fd) + 1
                    line = (json.dumps(entry, default=str, separators=(",", ":")) + "\n").encode()
                    os.write(fd, line)
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        return entry

    @contextmanager
    def span(self, agent: str, action: str, **fields: Any) -> Iterator[dict[str, Any]]:
        """Record duration and any exception. Yielded dict may be updated with extra fields."""
        extra: dict[str, Any] = dict(fields)
        t0 = time.perf_counter()
        try:
            yield extra
        except BaseException as e:
            extra["status"] = "error"
            extra["error"] = f"{type(e).__name__}: {e}"
            self.record(agent, action, duration_ms=(time.perf_counter() - t0) * 1000, **extra)
            raise
        else:
            extra.setdefault("status", "ok")
            self.record(agent, action, duration_ms=(time.perf_counter() - t0) * 1000, **extra)


def read_audit(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        if ln.strip():
            out.append(json.loads(ln))
    return out


def summarize_audit(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "entries": len(entries),
        "commands": len({e["command"] for e in entries if e.get("command")}),
        "tokens_in": sum(e.get("tokens_in") or 0 for e in entries),
        "tokens_out": sum(e.get("tokens_out") or 0 for e in entries),
        "cost_usd": round(sum(e.get("cost_usd") or 0.0 for e in entries), 6),
        "errors": sum(1 for e in entries if e.get("status") == "error"),
    }


def render_audit(path: str | Path, fmt: str = "table") -> str:
    entries = read_audit(path)
    totals = summarize_audit(entries)
    if fmt == "json":
        return json.dumps({"entries": entries, "totals": totals}, indent=2)
    if fmt != "table":
        raise ValueError(f"unknown format: {fmt}")
    cols = ["seq", "ts", "agent", "action", "command", "model", "tokens_in", "tokens_out", "cost_usd", "duration_ms"]
    rows = []
    for e in entries:
        r = []
        for c in cols:
            v = e.get(c)
            if isinstance(v, float):
                v = f"{v:.4f}" if c == "cost_usd" else f"{v:.1f}"
            r.append("" if v is None else str(v))
        rows.append(r)
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    fmt_row = lambda r: "  ".join(x.ljust(w) for x, w in zip(r, widths)).rstrip()
    lines = [fmt_row(cols), fmt_row(["-" * w for w in widths]), *map(fmt_row, rows)]
    lines.append("")
    lines.append(
        f"totals: {totals['entries']} entries, {totals['commands']} commands, "
        f"tokens in/out {totals['tokens_in']}/{totals['tokens_out']}, "
        f"cost ${totals['cost_usd']:.4f}, errors {totals['errors']}"
    )
    return "\n".join(lines)
