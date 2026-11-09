"""D-04: file-hash invalidation, dependents, stable IDs, cumulative cost."""
import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from migration_agent.analyze.incremental import Manifest, hash_file, load_cost_total
from migration_agent.analyze.runner import estimate, run_analyze
from migration_agent.cli import main
from migration_agent.graph import load_latest
from migration_agent.llm.base import LLMResponse
from migration_agent.llm.offline import OfflineLLM
from migration_agent.pipeline import run_extract
from migration_agent.project import Project

CTX = 14_000  # per-chunk budget of 400 tokens: one small directory per chunk


class CountingLLM(OfflineLLM):
    """OfflineLLM that bills 0.01 USD per call and records which chunk each call was for."""

    def complete(self, **kw):
        r = super().complete(**kw)
        self.calls[-1]["chunk"] = next((w for w in kw["prompt"].split() if w.startswith("CH-")), "")
        return LLMResponse(text=r.text, model="fake", tokens_in=10, tokens_out=5, cost_usd=0.01)


def body(name: str, imp: str = "", extra: str = "") -> str:
    return imp + "".join(f"# {name} filler line {j}\n" for j in range(30)) + f"def {name}():\n    return 1\n{extra}"


def make(tmp_path: Path) -> Project:
    proj = Project.init(tmp_path / "p.migration", "src", "p")
    files = {
        "a/x.py": body("x"),
        "b/y.py": body("y", "from a.x import x\n"),
        "c/z.py": body("z", "from b.y import y\n"),
        "d/w.py": body("w"),
    }
    for rel, text in files.items():
        f = proj.code / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    run_extract(proj)
    return proj


def ids(proj: Project) -> dict:
    return {(n.type, n.module, n.qualified_symbol): n.id for n in load_latest(proj.graph_dir).nodes}


def chunks_called(llm) -> set[str]:
    return {c["chunk"] for c in llm.calls}


def test_first_run_writes_manifest_with_hashes(tmp_path):
    proj = make(tmp_path)
    llm = CountingLLM()
    out = run_analyze(proj, llm, context_tokens=CTX)
    assert out["chunks"] == 4 and len(llm.calls) == 8
    m = Manifest.load(proj.root / "checkpoint" / "manifest.json")
    assert m.files["a/x.py"] == hash_file(proj.code / "a/x.py")
    assert len(m.chunks) == 4 and m.prompt_version and m.deps["b/y.py"] == ["a/x.py"]


def test_unchanged_repo_makes_zero_calls(tmp_path):
    proj = make(tmp_path)
    run_analyze(proj, CountingLLM(), context_tokens=CTX)
    before = ids(proj)
    llm = CountingLLM()
    out = run_analyze(proj, llm, context_tokens=CTX, incremental=True)
    assert llm.calls == [] and out["reused_chunks"] == 4 and out["analyzed_chunks"] == 0
    assert ids(proj) == before
    assert estimate(proj, incremental=True, context_tokens=CTX)[1] == 0.0


def test_one_changed_file_reanalyses_its_chunk_and_direct_dependents_only(tmp_path):
    proj = make(tmp_path)
    run_analyze(proj, CountingLLM(), context_tokens=CTX)
    before = ids(proj)
    (proj.code / "a/x.py").write_text(body("x", extra="# edited\n"))
    llm = CountingLLM()
    out = run_analyze(proj, llm, context_tokens=CTX, incremental=True)
    # a/x.py (changed) + b/y.py (imports it); c/z.py only imports b/y.py and d/w.py is unrelated
    assert out["analyzed_chunks"] == 2 and len(llm.calls) == 4 and out["reused_chunks"] == 2
    assert out["analyzed_chunks"] + out["reused_chunks"] == out["chunks"]
    after = ids(proj)
    assert all(after[k] == v for k, v in before.items() if k in after)   # unchanged nodes keep their IDs
    assert set(before) <= set(after)


def test_non_incremental_rerun_only_redoes_changed_chunk(tmp_path):
    proj = make(tmp_path)
    run_analyze(proj, CountingLLM(), context_tokens=CTX)
    (proj.code / "d/w.py").write_text(body("w", extra="# edited\n"))
    run_extract(proj)
    llm = CountingLLM()
    run_analyze(proj, llm, context_tokens=CTX)     # plain resume still detects stale chunks by hash
    assert len(llm.calls) == 2


def test_added_and_removed_files(tmp_path):
    proj = make(tmp_path)
    run_analyze(proj, CountingLLM(), context_tokens=CTX)
    before = ids(proj)
    (proj.code / "d/w.py").unlink()
    (proj.code / "e").mkdir()
    (proj.code / "e/v.py").write_text(body("v", "from d.w import w\n"))   # new file (its import no longer resolves)
    llm = CountingLLM()
    out = run_analyze(proj, llm, context_tokens=CTX, incremental=True)
    after = ids(proj)
    assert not any("d/w.py" in k[2] for k in after), "removed files drop their nodes"
    assert any("e/v.py" in k[2] for k in after)
    assert out["reused_chunks"] >= 3 and len(llm.calls) == 2               # only the new chunk is analysed
    assert all(after[k] == v for k, v in before.items() if k in after)
    new_ids = [v for k, v in after.items() if k not in before]
    old_max = max(int(v.split("-")[1]) for v in before.values() if v.startswith("COMP"))
    assert all(int(v.split("-")[1]) > old_max for v in new_ids if v.startswith("COMP"))   # never reused


def test_chunk_renumbering_does_not_force_reanalysis(tmp_path):
    proj = make(tmp_path)
    run_analyze(proj, CountingLLM(), context_tokens=CTX)
    (proj.code / "0").mkdir()
    (proj.code / "0/first.py").write_text(body("first"))          # sorts before everything -> shifts CH ids
    llm = CountingLLM()
    out = run_analyze(proj, llm, context_tokens=CTX, incremental=True)
    assert out["chunks"] == 5 and len(llm.calls) == 2 and out["reused_chunks"] == 4
    patches = sorted((proj.root / "checkpoint" / "chunks").glob("*.json"))
    assert len(patches) == 5
    for p in patches:
        assert json.loads(p.read_text())["chunk_id"] == p.stem


def test_prompt_version_change_invalidates_everything(tmp_path):
    proj = make(tmp_path)
    run_analyze(proj, CountingLLM(), context_tokens=CTX)
    mp = proj.root / "checkpoint" / "manifest.json"
    m = json.loads(mp.read_text())
    m["prompt_version"] = "ancient"
    mp.write_text(json.dumps(m))
    llm = CountingLLM()
    run_analyze(proj, llm, context_tokens=CTX, incremental=True)
    assert len(llm.calls) == 8


def test_cost_accumulates_across_sessions(tmp_path):
    proj = make(tmp_path)
    o1 = run_analyze(proj, CountingLLM(), context_tokens=CTX)
    assert o1["session_cost_usd"] == 0.08 and o1["total_cost_usd"] == 0.08
    (proj.code / "d/w.py").write_text(body("w", extra="# edited\n"))
    o2 = run_analyze(proj, CountingLLM(), context_tokens=CTX, incremental=True)
    assert o2["session_cost_usd"] == 0.02 and abs(o2["total_cost_usd"] - 0.10) < 1e-9
    t = load_cost_total(proj.audit_dir)
    assert abs(t["usd"] - 0.10) < 1e-9 and t["calls"] == 10 and len(t["sessions"]) == 2
    assert json.loads((proj.audit_dir / "cost_total.json").read_text())["usd"] == t["usd"]


def test_cli_incremental_flag_end_to_end(tmp_path):
    src = tmp_path / "svc"
    (src / "a").mkdir(parents=True)
    (src / "a/x.py").write_text(body("x"))
    (src / "b").mkdir()
    (src / "b/y.py").write_text(body("y", "from a.x import x\n"))
    proj = tmp_path / "svc.migration"
    r = CliRunner()
    for args in (["init", str(src), "--project", str(proj)], ["ingest", str(proj)], ["analyze", str(proj), "--offline"]):
        res = r.invoke(main, args)
        assert res.exit_code == 0, res.output
    (src / "b/y.py").write_text(body("y", "from a.x import x\n", "# updated\n"))
    shutil.rmtree(proj / "code")   # `ingest` needs an empty destination
    assert r.invoke(main, ["ingest", str(proj)]).exit_code == 0
    res = r.invoke(main, ["analyze", str(proj), "--offline", "--incremental"])
    assert res.exit_code == 0, res.output
    assert "changed file(s)" in res.output and "cost: this session" in res.output
    assert "'analyzed_chunks'" in res.output
