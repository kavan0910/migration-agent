import json
from pathlib import Path

from click.testing import CliRunner

from migration_agent.analyze.runner import run_analyze
from migration_agent.cli import main
from migration_agent.graph import load_latest
from migration_agent.llm.base import BudgetExceeded, FakeLLM
from migration_agent.llm.offline import OfflineLLM
from migration_agent.project import Project

REPO = Path(__file__).parent.parent / "eval" / "repos" / "express-crud"


def _prepare(tmp_path):
    proj = tmp_path / "p.migration"
    r = CliRunner()
    for args in (["init", str(REPO), "--project", str(proj)], ["ingest", str(proj)], ["extract", str(proj)]):
        assert r.invoke(main, args).exit_code == 0
    return proj


def test_analyze_offline_cli_produces_doc_graph_and_full_coverage(tmp_path):
    proj = _prepare(tmp_path)
    res = CliRunner().invoke(main, ["analyze", str(proj), "--offline"])
    assert res.exit_code == 0, res.output
    assert "estimated cost" in res.output and "'coverage_complete': True" in res.output
    doc = (proj / "docs" / "understanding.latest.md").read_text()
    assert doc.count("\n## ") >= 15 and "COMP-0001" in doc
    page = (proj / "docs" / "graph.html").read_text()
    assert "graph-data" in page and "COMP-0001" in page
    assert (proj / "checkpoint" / "chunks" / "CH-0001.json").exists()


def test_dry_run_only_estimates(tmp_path):
    proj = _prepare(tmp_path)
    res = CliRunner().invoke(main, ["analyze", str(proj), "--offline", "--dry-run"])
    assert res.exit_code == 0 and "plan:" in res.output
    assert not (proj / "docs" / "graph.html").exists()


def test_hallucinated_evidence_is_rejected_and_rules_survive(tmp_path):
    proj = _prepare(tmp_path)

    class Scripted(OfflineLLM):
        def complete(self, **kw):
            if kw["task"] == "business_rules":
                good = next(f for f in (REPO / "src").rglob("*.js"))
                rel = str(good.relative_to(REPO))
                first = next(l for l in good.read_text().splitlines() if l.strip())
                return type("R", (), {"text": json.dumps({"chunk_id": "x", "nodes": [
                    {"type": "business_rule", "module": "src", "qualified_symbol": "ok#rule", "name": "Real rule",
                     "evidence": [{"file": rel, "line_start": 1, "line_end": 1, "quote": first.strip()}]},
                    {"type": "business_rule", "module": "src", "qualified_symbol": "fake#rule", "name": "Hallucinated",
                     "evidence": [{"file": "src/does_not_exist.js", "line_start": 1, "line_end": 2}]}],
                    "edges": [], "unresolved": []}), "model": "t", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0, "cached": False})()
            return super().complete(**kw)

    run_analyze(Project.load(proj), Scripted())
    names = {n.name for n in load_latest(proj / "graph").nodes}
    assert "Real rule" in names and "Hallucinated" not in names


def test_resume_skips_completed_chunks(tmp_path):
    proj = _prepare(tmp_path)
    first = OfflineLLM()
    run_analyze(Project.load(proj), first)
    second = OfflineLLM()
    logs = []
    run_analyze(Project.load(proj), second, log=logs.append)
    assert second.calls == [] and any("resumed" in l for l in logs)


def test_budget_exceeded_stops_gracefully_and_still_renders(tmp_path):
    proj = _prepare(tmp_path)

    def boom(task, system, prompt):
        raise BudgetExceeded("budget $0.00 reached")

    out = run_analyze(Project.load(proj), FakeLLM(boom))
    assert out["stopped"] and "budget" in out["stopped"]
    assert (proj / "docs" / "understanding.latest.md").exists()
