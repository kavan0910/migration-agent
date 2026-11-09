"""D-04 incremental re-analysis: per-file content hashes + chunk-plan manifest, invalidation planning,
and cumulative cost across sessions.

checkpoint/manifest.json   file hashes, import deps, chunk_id -> files/signature, prompt_version, model
audit/cost_total.json      project-level cost totals accumulated over every analyze session
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from ..extract.deps import extract_deps
from .chunker import Chunk

MANIFEST_VERSION = 1


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def hash_files(code_dir: Path, paths: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(paths):
        try:
            out[p] = hash_file(Path(code_dir) / p)
        except OSError:
            continue
    return out


def compute_deps(code_dir: Path, paths: list[str]) -> dict[str, list[str]]:
    """file -> sorted files it imports (resolved inside the repo)."""
    out: dict[str, set[str]] = {}
    for s, d, _ln in extract_deps(Path(code_dir), sorted(paths)):
        out.setdefault(s, set()).add(d)
    return {k: sorted(v) for k, v in sorted(out.items())}


def chunk_signature(chunk: Chunk, hashes: dict[str, str]) -> str:
    """Identity of a chunk's analysed content: which files/line ranges, with which content hashes."""
    items = [[p, chunk.ranges.get(p) or [], hashes.get(p, "")] for p in sorted(chunk.files)]
    return hashlib.sha256(json.dumps(items, separators=(",", ":")).encode()).hexdigest()


class ChunkEntry(BaseModel):
    files: list[str]
    signature: str
    est_tokens: int = 0
    order: int = 0


class Manifest(BaseModel):
    version: int = MANIFEST_VERSION
    prompt_version: str = ""
    model: str = ""
    files: dict[str, str] = Field(default_factory=dict)          # path -> sha256
    deps: dict[str, list[str]] = Field(default_factory=dict)     # path -> imported paths
    chunks: dict[str, ChunkEntry] = Field(default_factory=dict)  # chunk id -> entry

    @classmethod
    def load(cls, path: Path) -> "Manifest | None":
        try:
            m = cls.model_validate_json(Path(path).read_text())
        except (OSError, ValueError):
            return None
        return m if m.version == MANIFEST_VERSION else None

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            f.write(self.model_dump_json())
        os.replace(tmp, path)


@dataclass
class Plan:
    chunks: list[Chunk]
    hashes: dict[str, str]
    deps: dict[str, list[str]]
    reuse: dict[str, str] = field(default_factory=dict)   # new chunk id -> old chunk id whose patch is reused
    pending: list[str] = field(default_factory=list)      # new chunk ids that must be (re-)analysed
    changed_files: set[str] = field(default_factory=set)  # added / modified / removed since the manifest
    dependent_files: set[str] = field(default_factory=set)  # files importing a changed file
    reason: str = ""

    @property
    def pending_chunks(self) -> list[Chunk]:
        p = set(self.pending)
        return [c for c in self.chunks if c.id in p]


def build_manifest(plan: Plan, prompt_version: str, model: str) -> Manifest:
    return Manifest(
        prompt_version=prompt_version, model=model, files=dict(plan.hashes), deps=dict(plan.deps),
        chunks={c.id: ChunkEntry(files=list(c.files), signature=chunk_signature(c, plan.hashes),
                                 est_tokens=c.est_tokens, order=c.order) for c in plan.chunks})


def plan_reuse(chunks: list[Chunk], hashes: dict[str, str], deps: dict[str, list[str]],
               old: Manifest | None, have_patch: set[str], failed: set[str], *,
               prompt_version: str, incremental: bool) -> Plan:
    """Decide which chunks keep their saved patch.

    default mode: a chunk is resumed when the same chunk id has an identical signature (content unchanged).
    incremental : additionally match by signature across renumbered ids, and invalidate dependents -
                  chunks containing a file that imports a changed/added/removed file are re-analysed too."""
    plan = Plan(chunks=chunks, hashes=hashes, deps=deps)
    if old is not None:
        plan.changed_files = {f for f in set(old.files) | set(hashes) if old.files.get(f) != hashes.get(f)}
        if incremental and plan.changed_files:
            for src, dsts in list(deps.items()) + list(old.deps.items()):
                if any(d in plan.changed_files for d in dsts):
                    plan.dependent_files.add(src)
    if old is None:
        plan.reason = "no manifest: analysing everything"
    elif old.prompt_version != prompt_version:
        plan.reason = "prompt version changed: analysing everything"
    else:
        usable = {cid: e for cid, e in old.chunks.items() if cid in have_patch and cid not in failed}
        by_sig: dict[str, list[str]] = {}
        for cid in sorted(usable):
            by_sig.setdefault(usable[cid].signature, []).append(cid)
        dirty = plan.changed_files | plan.dependent_files
        for c in chunks:
            sig = chunk_signature(c, hashes)
            if incremental:
                if any(f in dirty for f in c.files):
                    continue
                cands = by_sig.get(sig)
                if cands:
                    plan.reuse[c.id] = cands.pop(0)
            else:
                e = usable.get(c.id)
                if e is not None and e.signature == sig:
                    plan.reuse[c.id] = c.id
        plan.reason = "reusing unchanged chunks"
    plan.pending = [c.id for c in chunks if c.id not in plan.reuse]
    return plan


# ---- cumulative cost across sessions -------------------------------------------------------------
def cost_total_path(audit_dir: Path) -> Path:
    return Path(audit_dir) / "cost_total.json"


def load_cost_total(audit_dir: Path) -> dict:
    try:
        d = json.loads(cost_total_path(audit_dir).read_text())
        if isinstance(d, dict):
            return d
    except (OSError, ValueError):
        pass
    return {"usd": 0.0, "tokens_in": 0, "tokens_out": 0, "calls": 0, "sessions": []}


def record_session_cost(audit_dir: Path, *, usd: float, tokens_in: int, tokens_out: int, calls: int,
                        **extra) -> dict:
    """Add this session to audit/cost_total.json and return the new project-level totals."""
    t = load_cost_total(audit_dir)
    t["usd"] = round(float(t.get("usd", 0.0)) + usd, 6)
    t["tokens_in"] = int(t.get("tokens_in", 0)) + tokens_in
    t["tokens_out"] = int(t.get("tokens_out", 0)) + tokens_out
    t["calls"] = int(t.get("calls", 0)) + calls
    sessions = list(t.get("sessions") or [])
    sessions.append({"ts": datetime.now(timezone.utc).isoformat(), "usd": round(usd, 6),
                     "tokens_in": tokens_in, "tokens_out": tokens_out, "calls": calls, **extra})
    t["sessions"] = sessions[-200:]
    p = cost_total_path(audit_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(t, f, indent=2)
    os.replace(tmp, p)
    return t
