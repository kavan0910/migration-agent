"""A stand-in for migration_agent.service with the contract-section-2 signatures. No LLM, no network."""
from __future__ import annotations

import io
import json
import shutil
import tarfile
import threading
from pathlib import Path
from types import SimpleNamespace


def _res(stage, status="ok", summary=None, cost=0.0, error=None):
    return SimpleNamespace(stage=stage, status=status, summary=summary or {}, cost_usd=cost, error=error)


class FakeEngine:
    version = "fake-1.0"

    def __init__(self):
        self.chunks = 4
        self.cost_per_chunk = 0.10
        self.fail = {}            # stage -> (list of errors to raise/return in order)
        self.crash = {}           # stage -> exceptions to raise N times
        self.calls = []           # (stage, kwargs-without-secrets)
        self.configs = []         # configs seen (to assert key injection)
        self.on_stage_hook = None  # callable(stage, project_dir) for tests to interfere mid-run
        self.under_report_cost = False
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ engine API
    def run_stage(self, project_dir, stage, *, source=None, config=None, max_usd=None, workers=1, incremental=False,
                  offline=False, on_event=None, llm=None, finding_id=None, status=None, note=None, reanalyze=False):
        project_dir = Path(project_dir)
        with self.lock:
            self.calls.append((stage, {"incremental": incremental, "max_usd": max_usd, "reanalyze": reanalyze,
                                       "source": source}))
            self.configs.append(dict(config or {}))
        emit = on_event or (lambda e: None)
        if self.on_stage_hook:
            self.on_stage_hook(stage, project_dir)
        if self.crash.get(stage):
            exc = self.crash[stage].pop(0)
            raise exc
        if self.fail.get(stage):
            return _res(stage, "failed", error=self.fail[stage].pop(0))
        if stage == "ingest":
            project_dir.mkdir(parents=True, exist_ok=True)
            code = project_dir / "code"
            if code.exists():
                shutil.rmtree(code)
            shutil.copytree(source, code)
            (project_dir / "project.json").write_text(json.dumps({"name": "p", "source": "x"}))
            for d in ("graph/snapshots", "docs", "feedback", "user_docs"):
                (project_dir / d).mkdir(parents=True, exist_ok=True)
            n = sum(1 for p in code.rglob("*") if p.is_file())
            emit({"type": "progress", "stage": stage, "done": 1, "total": 1})
            return _res(stage, summary={"files_copied": n})
        if stage == "extract":
            (project_dir / "facts.json").write_text("{}")
            return _res(stage, summary={"items": 3})
        if stage == "analyze":
            return self._analyze(project_dir, emit, max_usd, config)
        if stage == "verify":
            if not (project_dir / "docs" / "understanding.latest.md").exists():
                return _res(stage, "failed", error="No understanding doc yet. Run analyze first.")
            docs = sorted(p.name for p in (project_dir / "user_docs").glob("*"))
            self._write_findings(project_dir, docs)
            return _res(stage, summary={"findings": 2})
        if stage == "resolve":
            if reanalyze:
                v = self._versions(project_dir)
                self._write_snapshot(project_dir, (v[-1] if v else 0) + 1)
                (project_dir / "docs" / "changelog.md").write_text("# Changelog\n- reanalyzed\n")
                return _res(stage, summary={"new_version": (v[-1] if v else 0) + 1})
            return _res(stage)
        raise ValueError(stage)

    def _analyze(self, pdir, emit, max_usd, config):
        spent = 0.0
        model = (config or {}).get("models", {}).get("chunk_extract", "m")
        for i in range(self.chunks):
            if max_usd is not None and spent + self.cost_per_chunk > max_usd + 1e-12:
                return _res("analyze", "stopped_budget", cost=spent, error="budget reached")
            spent += self.cost_per_chunk
            if not self.under_report_cost:
                emit({"type": "usage", "stage": "analyze", "task": "chunk_extract", "model": model,
                      "input_tokens": 1000, "output_tokens": 200, "usd": self.cost_per_chunk})
            emit({"type": "progress", "stage": "analyze", "done": i + 1, "total": self.chunks})
            emit({"type": "log", "level": "info", "message": f"chunk {i + 1} done"})
        v = self._versions(pdir)
        self._write_snapshot(pdir, (v[-1] if v else 0) + 1)
        (pdir / "docs" / "understanding.latest.md").write_text("# Understanding\n\nHello **world**.\n")
        self._write_findings(pdir, [])
        return _res("analyze", cost=spent, summary={"nodes": 3})

    def _write_snapshot(self, pdir, v):
        snap = {"version": v, "nodes": [
            {"id": "COMP-0001", "type": "component", "name": "services", "responsibility": "business logic"},
            {"id": "EP-0001", "type": "endpoint", "name": "GET /books", "responsibility": "list books"},
            {"id": "ENT-0001", "type": "data_entity", "name": "Book"}] + ([{"id": "RULE-0001", "type": "business_rule", "name": f"rule v{v}"}] if v > 1 else []),
            "edges": [{"id": "EDGE-0001", "source": "EP-0001", "target": "COMP-0001", "type": "calls"}]}
        (pdir / "graph" / "snapshots").mkdir(parents=True, exist_ok=True)
        (pdir / "graph" / "snapshots" / f"v{v:03d}.json").write_text(json.dumps(snap))

    def _versions(self, pdir):
        d = Path(pdir) / "graph" / "snapshots"
        return sorted(int(p.stem[1:]) for p in d.glob("v*.json")) if d.exists() else []

    def _write_findings(self, pdir, docs):
        f = {"findings": [{"id": "F-0001", "kind": "contradiction", "summary": "doc says X, code says Y", "item_ids": ["COMP-0001"]},
                          {"id": "F-0002", "kind": "undocumented", "summary": "nothing documents Book", "item_ids": ["ENT-0001"]}],
             "docs_seen": docs}
        (pdir / "feedback").mkdir(parents=True, exist_ok=True)
        (pdir / "feedback" / "findings.json").write_text(json.dumps(f))

    def estimate(self, project_dir, config=None, incremental=False):
        return {"chunks": self.chunks, "usd": round(self.chunks * self.cost_per_chunk, 6)}

    def _read(self, pdir, name, default):
        p = Path(pdir) / "feedback" / name
        return json.loads(p.read_text()) if p.exists() else default

    def review_state(self, project_dir):
        return {"project": "p", "findings": self._read(project_dir, "findings.json", {"findings": []}),
                "resolutions": self._read(project_dir, "resolutions.json", {"resolutions": []}),
                "comments": self._read(project_dir, "comments.json", {"comments": []}),
                "versions": self._versions(project_dir),
                "known_ids": ["COMP-0001", "EP-0001", "ENT-0001", "F-0001", "F-0002"], "errors": []}

    def graph_snapshot(self, project_dir, version=None):
        v = self._versions(project_dir)
        if not v:
            return {"version": 0, "nodes": [], "edges": []}
        n = v[-1] if version is None else version
        return json.loads((Path(project_dir) / "graph" / "snapshots" / f"v{n:03d}.json").read_text())

    def graph_versions(self, project_dir):
        return [{"version": v, "nodes": len(self.graph_snapshot(project_dir, v)["nodes"]), "edges": 1}
                for v in self._versions(project_dir)]

    def add_comment(self, project_dir, item_id, text, author=None):
        d = self._read(project_dir, "comments.json", {"comments": []})
        d["comments"].append({"item_id": item_id, "text": text, "author": author})
        (Path(project_dir) / "feedback" / "comments.json").write_text(json.dumps(d))
        return d["comments"][-1]

    def add_resolution(self, project_dir, finding_id, status, note="", author=None):
        d = self._read(project_dir, "resolutions.json", {"resolutions": []})
        d["resolutions"].append({"finding_id": finding_id, "status": status, "note": note})
        (Path(project_dir) / "feedback" / "resolutions.json").write_text(json.dumps(d))
        return d["resolutions"][-1]

    def document(self, project_dir):
        p = Path(project_dir) / "docs" / "understanding.latest.md"
        return p.read_text() if p.exists() else ""

    def changelog(self, project_dir):
        p = Path(project_dir) / "docs" / "changelog.md"
        return p.read_text() if p.exists() else ""

    def pack_project(self, project_dir, *, include_cache=True):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(project_dir, arcname=".")
        return buf.getvalue()

    def unpack_project(self, data, dest):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(dest, filter="data")
