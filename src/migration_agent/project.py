"""Project folder layout: the versioned folder is the only state (A-01).

<project>/
  project.json          metadata (name, source, created, schema_version)
  code/                 normalized read-only copy of the service (A-02)
  facts/                ast.json, manifests.json, config.json (A-03..A-05)
  inventory.json        coverage inventory (A-06)
  graph/                snapshots/, registry.json, latest.json (A-08)
  docs/                 understanding.latest.md (A-10)
  user_docs/            documents for the Verifier only (Phase C)
  feedback/             comments, resolutions (Phase C)
  audit/                audit.jsonl (A-07)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

SCHEMA_VERSION = 1
SUBDIRS = ("code", "facts", "graph", "graph/snapshots", "docs", "user_docs", "feedback", "audit")


class ProjectMeta(BaseModel):
    name: str
    source: str
    created: str
    schema_version: int = SCHEMA_VERSION


class Project:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    code = property(lambda s: s.root / "code")
    facts = property(lambda s: s.root / "facts")
    graph_dir = property(lambda s: s.root / "graph")
    docs = property(lambda s: s.root / "docs")
    audit_dir = property(lambda s: s.root / "audit")
    meta_path = property(lambda s: s.root / "project.json")
    inventory_path = property(lambda s: s.root / "inventory.json")

    @classmethod
    def init(cls, root: Path, source: str, name: str | None = None) -> "Project":
        p = cls(root)
        if p.meta_path.exists():
            raise FileExistsError(f"{p.meta_path} already exists")
        for d in SUBDIRS:
            (p.root / d).mkdir(parents=True, exist_ok=True)
        meta = ProjectMeta(
            name=name or p.root.name,
            source=str(source),
            created=datetime.now(timezone.utc).isoformat(),
        )
        p.meta_path.write_text(meta.model_dump_json(indent=2))
        return p

    @classmethod
    def load(cls, root: Path) -> "Project":
        p = cls(root)
        if not p.meta_path.exists():
            raise FileNotFoundError(f"not a project folder (no project.json): {p.root}")
        return p

    def meta(self) -> ProjectMeta:
        return ProjectMeta.model_validate_json(self.meta_path.read_text())
