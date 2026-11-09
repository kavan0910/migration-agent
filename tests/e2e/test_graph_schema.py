import json
import re

import pytest

from migration_agent.analyze.coverage import check_coverage
from migration_agent.analyze.evidence import assert_all_evidence_valid
from migration_agent.evalharness import EVAL_REPOS, load_actual, load_expected, score, compare, Report
from migration_agent.graph import diff_graphs, load_snapshot
from migration_agent.graph.models import EDGE_PREFIX, ID_PREFIX, Graph
from migration_agent.pipeline import Inventory

import e2e_golden as golden

# Documented floors for the replay LLM (the replay emits every expected item, so these are generous; the
# remaining gap is static-extractor extras and "handled_by" edges that the graph model cannot express).
PRECISION_FLOOR = {"components": 0.5, "endpoints": 0.8, "entities": 0.9, "rules": 0.9, "edges": 0.4}
RECALL_FLOOR = {"components": 0.95, "endpoints": 0.95, "entities": 0.95, "rules": 0.95, "edges": 0.8}
F1_TOLERANCE = 0.05


def test_json_schema_is_stable():
    golden.check("graph.schema.json", Graph.model_json_schema())


def test_latest_snapshot_validates_against_models(run):
    raw = json.loads((run.proj.graph_dir / "latest.json").read_text())
    assert set(raw) >= {"version", "path"}
    snap = json.loads((run.proj.graph_dir / raw["path"]).read_text())
    assert set(snap) == {"version", "nodes", "edges"}
    for n in snap["nodes"]:
        assert {"id", "type", "name", "module", "qualified_symbol", "responsibility", "evidence",
                "inventory_refs", "quirk", "suspected_bug", "confidence"} <= set(n)
    g = run.graph()
    assert Graph.model_validate(g.model_dump()) == g
    assert g.version == raw["version"] == run.versions["resolve2"]


def test_id_formats(run):
    g = run.graph()
    assert g.nodes and g.edges
    for n in g.nodes:
        assert re.fullmatch(rf"{ID_PREFIX[n.type]}-\d{{4,}}", n.id), n.id
    for e in g.edges:
        assert re.fullmatch(rf"{EDGE_PREFIX}-\d{{4,}}", e.id), e.id
    assert len({n.id for n in g.nodes}) == len(g.nodes) and len({e.id for e in g.edges}) == len(g.edges)
    known = {n.id for n in g.nodes}
    assert all(e.source in known and e.target in known and e.source != e.target for e in g.edges)


def test_graph_has_expected_node_types(run):
    types = {n.type for n in run.graph().nodes}
    assert {"component", "endpoint", "data_entity", "business_rule"} <= types


def _shape(g):
    return ([(n.id, n.type, n.module, n.qualified_symbol, n.name) for n in g.nodes],
            [(e.id, e.source, e.type, e.target) for e in g.edges])


def test_ids_stable_across_identical_second_run(run):
    first = load_snapshot(run.proj.graph_dir, run.versions["analyze"])
    second = load_snapshot(run.rerun_root / "graph", run.versions["analyze"] + 1)
    third = load_snapshot(run.rerun2_root / "graph", run.versions["analyze"] + 2)
    assert _shape(first) == _shape(second) == _shape(third)
    assert (json.loads((run.rerun_root / "graph" / "registry.json").read_text())["entries"]
            == json.loads((run.rerun2_root / "graph" / "registry.json").read_text())["entries"])


def test_third_identical_run_is_pure_cache_hit(run):
    assert run.rerun2_calls_after == run.rerun2_calls_before
    cached = run.llm_calls(run.rerun2_root)[len(run.llm_calls(run.rerun_root)):]   # entries copied from run 2 excluded
    assert cached and all(e["cached"] for e in cached) and sum(e["cost_usd"] for e in cached) == 0


def test_second_identical_run_is_served_from_cache(run):
    assert run.rerun_calls_after == run.rerun_calls_before


def test_node_ids_survive_review_cycle(run):
    first = load_snapshot(run.proj.graph_dir, run.versions["analyze"])
    d = diff_graphs(first, run.graph())
    assert not d.removed_nodes and not d.added_nodes
    assert d.changed_nodes, "re-analysis should have changed at least one node"
    triples = lambda g: {(e.source, e.type, e.target) for e in g.edges}  # noqa: E731
    assert triples(first) == triples(run.graph())



def test_edge_ids_survive_review_cycle(run):
    first = load_snapshot(run.proj.graph_dir, run.versions["analyze"])
    d = diff_graphs(first, run.graph())
    assert not d.removed_edges and not d.added_edges


def test_evidence_valid(run):
    g = run.graph()
    rep = score(load_actual(run.proj.graph_dir), load_expected(run.repo))
    assert rep.evidence_validity == 1.0, rep.evidence_errors
    assert rep.injection_hits == []
    assert_all_evidence_valid(g, run.proj.code)         # also verifies symbol + verbatim quote
    assert all(n.evidence for n in g.nodes), [n.id for n in g.nodes if not n.evidence]
    assert all(e.evidence for e in g.edges)


def test_coverage_complete(run):
    inv = Inventory.model_validate_json(run.proj.inventory_path.read_text())
    cov = check_coverage(inv, run.graph())
    assert cov.complete and cov.unmapped == 0 and cov.mapped == len(inv.items)
    assert "'coverage_complete': True" in run.outputs["analyze"].output


def test_score_meets_floors_and_does_not_regress(run):
    rep = score(load_actual(run.proj.graph_dir), load_expected(run.repo))
    for cat, sc in rep.categories.items():
        assert sc.recall >= RECALL_FLOOR[cat], (cat, sc.recall, sc.missed)
        assert sc.precision >= PRECISION_FLOOR[cat], (cat, sc.precision, sc.spurious)
    assert rep.passed, rep.gates
    name = f"{run.repo}.score.json"
    if golden.updating():
        golden.write(name, rep.to_dict())
        return
    ok, problems = compare(Report.from_dict(golden.load(name)), rep, tolerance=F1_TOLERANCE)
    assert ok, problems


def test_expected_items_present_by_identity(run):
    g = run.graph()
    have = {(n.type, n.module, n.qualified_symbol) for n in g.nodes}
    exp = load_expected(run.repo)
    for r in exp["rules"]:
        assert ("business_rule", r["module"], r["qualified_symbol"]) in have
    for c in exp["entities"]:
        assert ("data_entity", c["module"], c["qualified_symbol"]) in have
    assert (EVAL_REPOS / run.repo).is_dir()
