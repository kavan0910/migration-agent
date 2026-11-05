import json

from migration_agent.analyze.crossref import resolve_cross_refs
from migration_agent.analyze.patch import NodeRef, UnresolvedRef
from migration_agent.graph.models import Evidence, Graph, Node
from migration_agent.llm.base import FakeLLM
from migration_agent.sandbox.tools import ReadOnlyTools


def n(i, sym, name=None, mod="m"):
    return Node(id=f"COMP-{i:04d}", type="component", name=name or sym.split(".")[-1], module=mod,
                qualified_symbol=sym)


def ref(name):
    return UnresolvedRef(from_node=NodeRef(type="component", module="m", qualified_symbol="A.run"), name=name,
                         evidence=[Evidence(file="a.py", line_start=1, quote="x")])


def setup(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    return ReadOnlyTools(tmp_path)


def test_deterministic_no_llm(tmp_path):
    g = Graph(nodes=[n(1, "pkg.Billing.charge"), n(2, "pkg.Mail.send")])
    llm = FakeLLM(lambda *a: 1 / 0)
    p = resolve_cross_refs(llm, g, [ref("pkg.Mail.send"), ref("charge")], setup(tmp_path))
    assert llm.calls == []
    assert [e.target.qualified_symbol for e in p.edges] == ["pkg.Mail.send", "pkg.Billing.charge"]
    assert p.edges[0].evidence and not p.unresolved


def test_ambiguous_goes_to_llm(tmp_path):
    g = Graph(nodes=[n(1, "a.Svc.save"), n(2, "b.Repo.save")])
    llm = FakeLLM(lambda t, s, p: json.dumps({"resolutions": [{"index": 0, "target_id": "COMP-0002"}]}))
    p = resolve_cross_refs(llm, g, [ref("save")], setup(tmp_path))
    assert len(llm.calls) == 1 and llm.calls[0]["task"] == "cross_ref"
    assert "COMP-0001" in llm.calls[0]["prompt"] and "<<<DATA" in llm.calls[0]["prompt"]
    assert p.edges[0].target.qualified_symbol == "b.Repo.save" and not p.unresolved


def test_llm_null_and_invalid_id_and_repair(tmp_path):
    g = Graph(nodes=[n(1, "a.Svc.save"), n(2, "b.Repo.save")])
    replies = iter(["not json", json.dumps({"resolutions": [{"index": 0, "target_id": "COMP-0001", "edge_type": "writes"}]})])
    llm = FakeLLM(lambda *a: next(replies))
    p = resolve_cross_refs(llm, g, [ref("save")], setup(tmp_path))
    assert len(llm.calls) == 2 and "invalid" in llm.calls[1]["prompt"]
    assert p.edges[0].type == "writes"
    llm2 = FakeLLM(lambda *a: json.dumps({"resolutions": [{"index": 0, "target_id": "NOPE"}]}))
    p2 = resolve_cross_refs(llm2, g, [ref("save")], setup(tmp_path))
    assert len(llm2.calls) == 2 and p2.edges == [] and len(p2.unresolved) == 1


def test_unmatched_stays_unresolved(tmp_path):
    g = Graph(nodes=[n(1, "a.Svc.save")])
    llm = FakeLLM(lambda *a: "{}")
    p = resolve_cross_refs(llm, g, [ref("zzz_unknown")], setup(tmp_path))
    assert p.unresolved[0].name == "zzz_unknown" and llm.calls == []
