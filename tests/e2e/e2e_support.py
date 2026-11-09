"""E2E support code (kept out of conftest.py so test modules can import it unambiguously).

Fixtures: drive the real CLI (click CliRunner) over the three reference repos with a replay LLM.

The replay LLM is injected at the transport level: ``migration_agent.llm.litellm_client.LiteLLMClient`` is
swapped for a factory that builds the *real* client with ``transport=ReplayBackend.transport``, so cost
accounting, the disk cache, the budget guard and the audit log all run for real.
"""
from __future__ import annotations

import functools
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from click.testing import CliRunner

from e2e_replay import ReplayBackend, user_doc_text, write_config
from migration_agent.cli import main
from migration_agent.evalharness import EVAL_REPOS
from migration_agent.graph import load_latest
from migration_agent.project import Project

REPOS = ["express-crud", "spring-lite", "django-lite"]
GOLDEN = Path(__file__).parent / "golden"


@dataclass
class Run:
    repo: str
    root: Path
    backend: ReplayBackend
    config: Path
    proj: Project
    outputs: dict = field(default_factory=dict)       # stage -> click Result
    versions: dict = field(default_factory=dict)      # stage -> graph version after the stage
    rerun_root: Path | None = None
    rerun2_root: Path | None = None
    rerun2_calls_before: int = 0
    rerun2_calls_after: int = 0
    rerun_calls_before: int = 0
    rerun_calls_after: int = 0
    analyze_calls: list = field(default_factory=list)
    comment_item: str = ""
    seconds: float = 0.0

    def graph(self):
        return load_latest(self.proj.graph_dir)

    @property
    def doc(self) -> str:
        return (self.root / "docs" / "understanding.latest.md").read_text()

    def audit_entries(self, root: Path | None = None) -> list[dict]:
        p = (root or self.root) / "audit" / "audit.jsonl"
        return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]

    def llm_calls(self, root: Path | None = None) -> list[dict]:
        return [e for e in self.audit_entries(root) if e["action"] == "llm_call"]


BACKENDS: dict[Path, ReplayBackend] = {}


def register(root: Path, backend: ReplayBackend) -> None:
    BACKENDS[Path(root).resolve()] = backend


def _factory(config=None, *, budget=None, cache_dir=None, audit=None, **kw):
    """Stand-in for LiteLLMClient: the real client wired to the replay backend of the project owning cache_dir."""
    from migration_agent.llm.litellm_client import _RealClient
    backend = BACKENDS[Path(cache_dir).resolve().parent]
    return _RealClient(config, budget=budget, cache_dir=cache_dir, audit=audit,
                       transport=backend.transport, sleep=lambda s: None, **kw)


def install_replay(mp: pytest.MonkeyPatch) -> None:
    from migration_agent.llm import litellm_client as lc
    if not hasattr(lc, "_RealClient"):
        mp.setattr(lc, "_RealClient", lc.LiteLLMClient, raising=False)
    mp.setattr(lc, "LiteLLMClient", _factory)
    mp.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")


def invoke(run: Run, stage: str, args: list[str], expect_ok: bool = True):
    res = CliRunner().invoke(main, args)
    run.outputs[stage] = res
    if expect_ok:
        assert res.exit_code == 0, f"{stage} failed: {res.output}\n{res.exception!r}"
    return res


_RUNS: dict[str, Run] = {}


def get_run(repo: str, base: Path, mp) -> Run:
    if repo not in _RUNS:
        r = make_run(repo, base, mp)
        full_flow(r)
        _RUNS[repo] = r
    return _RUNS[repo]




def make_run(repo: str, base: Path, mp: pytest.MonkeyPatch, tag: str = "main") -> Run:
    root = base / f"{repo}-{tag}.migration"
    backend = ReplayBackend(repo, root)
    register(root, backend)
    cfg = write_config(base / f"{repo}-{tag}.config.json")
    run = Run(repo, root, backend, cfg, Project(root))
    src = str(EVAL_REPOS / repo)
    invoke(run, "init", ["init", src, "--project", str(root)])
    run.proj = Project.load(root)
    invoke(run, "ingest", ["ingest", str(root)])
    invoke(run, "extract", ["extract", str(root)])
    return run


def full_flow(run: Run) -> None:
    """analyze -> (idempotency re-run in a copy) -> verify -> resolve x2 -> changelog."""
    root, cfg = str(run.root), str(run.config)
    t0 = time.perf_counter()
    invoke(run, "analyze", ["analyze", root, "--config", cfg])
    run.versions["analyze"] = run.graph().version
    run.analyze_calls = list(run.backend.calls)

    # identical second run: copy the project, drop the checkpoint, keep the LLM cache + registry
    rr = run.root.parent / f"{run.root.name}.rerun"
    shutil.copytree(run.root, rr)
    register(rr, run.backend)
    shutil.rmtree(rr / "checkpoint")
    run.rerun_root = rr
    run.rerun_calls_before = len(run.backend.calls)
    run.outputs["rerun"] = CliRunner().invoke(main, ["analyze", str(rr), "--config", cfg])
    assert run.outputs["rerun"].exit_code == 0, run.outputs["rerun"].output
    run.rerun_calls_after = len(run.backend.calls)
    # third run, from the second run's (now stable) registry: must be a pure cache hit
    rr2 = run.root.parent / f"{run.root.name}.rerun2"
    shutil.copytree(rr, rr2)
    register(rr2, run.backend)
    shutil.rmtree(rr2 / "checkpoint")
    run.rerun2_root = rr2
    run.rerun2_calls_before = len(run.backend.calls)
    run.outputs["rerun2"] = CliRunner().invoke(main, ["analyze", str(rr2), "--config", cfg])
    assert run.outputs["rerun2"].exit_code == 0, run.outputs["rerun2"].output
    run.rerun2_calls_after = len(run.backend.calls)

    (run.root / "user_docs" / "requirements.md").write_text(user_doc_text(run.repo))
    invoke(run, "verify", ["verify", root, "--config", cfg])
    findings = json.loads((run.root / "feedback" / "findings.json").read_text())["findings"]
    contra = next(f for f in findings if f["kind"] == "contradiction")
    invoke(run, "resolve1", ["resolve", root, contra["id"], "--status", "needs_reanalysis",
                             "--note", "please re-check this rule", "--config", cfg])
    run.versions["resolve1"] = run.graph().version
    from migration_agent.evalharness import load_expected
    with_code = {c["module"] for c in load_expected(run.repo)["components"]}      # skip empty files (__init__.py)
    comp = next(n for n in run.graph().nodes if n.type == "component" and n.module in with_code)
    run.comment_item = comp.id
    invoke(run, "resolve2", ["resolve", root, "--comment", comp.id, "--note", "what does this do?", "--config", cfg])
    run.versions["resolve2"] = run.graph().version
    invoke(run, "changelog", ["changelog", root])
    run.seconds = time.perf_counter() - t0
