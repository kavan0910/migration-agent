"""Helpers for red-team tests (recording transport, project builders)."""
from __future__ import annotations

import json
from pathlib import Path

# bound at import time: e2e fixtures monkeypatch the module attribute with a replay factory
from migration_agent.llm.litellm_client import Budget, LiteLLMClient


def chunk_response(prompt: str) -> str:
    """Offline-style extractor: one component per `=== path ===` block, verbatim first-line quote."""
    from migration_agent.llm.offline import OfflineLLM
    return OfflineLLM()._chunk_extract(prompt)


class RecordingTransport:
    """litellm.completion stand-in. Records every request; answers like the offline extractor."""

    def __init__(self, reply=None):
        self.requests: list[dict] = []
        self.reply = reply

    def __call__(self, **kw):
        self.requests.append(kw)
        msgs = kw["messages"]
        system = msgs[0]["content"]
        system = system if isinstance(system, str) else "".join(p["text"] for p in system)
        user = msgs[-1]["content"]
        if self.reply is not None:
            text = self.reply(system, user)
        elif "code-analysis engine" in system and "=== " in user:
            text = chunk_response(user)
        else:
            text = json.dumps({"nodes": [], "edges": [], "unresolved": [], "claims": [], "findings": []})
        return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    @property
    def all_text(self) -> str:
        return json.dumps(self.requests, default=str, ensure_ascii=False)


def tree_text(root: Path, skip: tuple[str, ...] = ()) -> dict[str, str]:
    """All readable file contents under root, by relative path (binary decoded leniently)."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not any(part in skip for part in p.relative_to(root).parts):
            out[p.relative_to(root).as_posix()] = p.read_bytes().decode("utf-8", "replace")
    return out


def make_project(tmp_path: Path, repo: Path, name: str = "proj"):
    """init -> ingest -> extract on a local directory source (no network, no LLM)."""
    from migration_agent.pipeline import run_extract, run_ingest
    from migration_agent.project import Project
    proj = Project.init(tmp_path / f"{name}.migration", str(repo))
    rep = run_ingest(proj, str(repo))
    (proj.root / "ingest_report.json").write_text(rep.model_dump_json(indent=2))
    run_extract(proj)
    return proj, rep


def llm_client(proj, transport, *, cache=True):
    """Real LiteLLMClient (cache + audit + budget) over a recording transport."""
    from migration_agent.audit import AuditLog
    return LiteLLMClient(budget=Budget(max_usd=5), cache_dir=(proj.root / "llm_cache") if cache else None,
                         audit=AuditLog(proj.audit_dir / "audit.jsonl"), transport=transport, sleep=lambda s: None)


def analyze(proj, transport):
    from migration_agent.analyze.runner import run_analyze
    return run_analyze(proj, llm_client(proj, transport))
