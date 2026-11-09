"""Append-only stable-ID registry: identity key -> ID, never reused or changed."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import EDGE_PREFIX, ID_PREFIX

SEP = "\x1f"


def node_key(type: str, module: str, qualified_symbol: str) -> str:
    if type not in ID_PREFIX:
        raise ValueError(f"unknown node type {type!r}")
    return SEP.join((type, module, qualified_symbol))


def edge_key(source: str, type: str, target: str) -> str:
    return SEP.join(("edge", source, type, target))


def _prefix(key: str) -> str:
    t = key.split(SEP, 1)[0]
    return EDGE_PREFIX if t == "edge" else ID_PREFIX[t]


class Registry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.entries: dict[str, str] = {}   # key -> id
        self.counters: dict[str, int] = {}  # prefix -> last number
        if self.path and self.path.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.path.read_text())
        self.entries = dict(data["entries"])
        self.counters = {k: int(v) for k, v in data["counters"].items()}
        ids = list(self.entries.values())
        if len(set(ids)) != len(ids):
            raise ValueError("corrupt registry: duplicate ids")

    def _alloc(self, key: str) -> str:
        if key in self.entries:
            return self.entries[key]
        p = _prefix(key)
        n = self.counters.get(p, 0) + 1
        self.counters[p] = n
        self.entries[key] = f"{p}-{n:04d}"
        return self.entries[key]

    def node_id(self, type: str, module: str, qualified_symbol: str) -> str:
        return self._alloc(node_key(type, module, qualified_symbol))

    def edge_id(self, source: str, type: str, target: str) -> str:
        return self._alloc(edge_key(source, type, target))

    def allocate_nodes(self, keys: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], str]:
        """Batch allocation: new keys are sorted first so IDs don't depend on input order."""
        for k in sorted(set(keys)):
            self._alloc(node_key(*k))
        return {k: self.entries[node_key(*k)] for k in keys}

    def allocate_edges(self, keys: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], str]:
        """keys are (source_id, edge_type, target_id)."""
        for k in sorted(set(keys)):
            self._alloc(edge_key(*k))
        return {k: self.entries[edge_key(*k)] for k in keys}

    def adopt_edge(self, source: str, type: str, target: str, edge_id: str) -> None:
        """Make (source, type, target) -> edge_id authoritative, as saved in a graph (e.g. after nodes were
        collapsed and an edge re-pointed), so later merges keep that ID. Any other key bound to the same
        ID, and any other ID previously bound to this key, is dropped (IDs are never reused)."""
        key = edge_key(source, type, target)
        if self.entries.get(key) == edge_id:
            return
        for k in [k for k, v in self.entries.items() if v == edge_id]:
            del self.entries[k]
        self.entries[key] = edge_id
        num = edge_id.rpartition("-")[2]
        if num.isdigit():
            self.counters[EDGE_PREFIX] = max(self.counters.get(EDGE_PREFIX, 0), int(num))

    def lookup(self, key: str) -> str | None:
        return self.entries.get(key)

    def save(self, path: str | Path | None = None) -> None:
        p = Path(path) if path else self.path
        if p is None:
            raise ValueError("no path")
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"counters": dict(sorted(self.counters.items())),
             "entries": dict(sorted(self.entries.items(), key=lambda kv: kv[1]))},
            indent=2, sort_keys=False,
        )
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".registry-")
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp, p)
        self.path = p
