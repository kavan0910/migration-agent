import pytest

from migration_agent.analyze.evidence import EvidenceError, assert_all_evidence_valid, verify_evidence
from migration_agent.graph.models import Edge, Evidence, Graph, Node


@pytest.fixture
def code(tmp_path):
    d = tmp_path / "code"
    d.mkdir()
    (d / "a.py").write_text("def foo():\n    return   1\n\nclass Bar:\n    pass\n")
    (tmp_path / "secret.py").write_text("def foo(): pass\n")
    return d


def node(i, ev, type="component", t="COMP", refs=()):
    return Node(id=f"{t}-{i:04d}", type=type, name="n", evidence=ev, inventory_refs=list(refs))


def run(code, ev):
    g = Graph(nodes=[node(1, [ev])])
    return verify_evidence(g, code)


def test_valid(code):
    r = run(code, Evidence(file="a.py", line_start=1, line_end=2, symbol="foo", quote="return 1"))
    assert not r.rejected and len(r.graph.nodes) == 1
    assert_all_evidence_valid(r.graph, code)


@pytest.mark.parametrize("ev,reason", [
    (Evidence(file="nope.py"), "does not exist"),
    (Evidence(file="a.py", line_start=99), "outside file"),
    (Evidence(file="a.py", line_start=1, line_end=9), "outside file"),
    (Evidence(file="a.py", line_start=1, line_end=2, symbol="NoSuchSymbol"), "symbol"),
    (Evidence(file="a.py", line_start=1, quote="class Bar"), "quote"),
    (Evidence(file="../secret.py", line_start=1, symbol="foo"), "escapes"),
    (Evidence(file="/etc/passwd"), "escapes"),
])
def test_failures_reject(code, ev, reason):
    r = run(code, ev)
    assert r.rejected == ["COMP-0001"] and not r.graph.nodes
    assert any(reason in e.reason for e in r.log if e.action == "evidence_dropped")


def test_partial_drop_and_edge_cascade(code):
    good = Evidence(file="a.py", line_start=4, symbol="Bar")
    bad = Evidence(file="x.py")
    g = Graph(nodes=[node(1, [good, bad]), node(2, [bad]), node(3, [good])],
              edges=[Edge(id="EDGE-0001", source="COMP-0001", target="COMP-0002", type="calls", evidence=[good]),
                     Edge(id="EDGE-0002", source="COMP-0001", target="COMP-0003", type="calls", evidence=[bad]),
                     Edge(id="EDGE-0003", source="COMP-0001", target="COMP-0003", type="reads", evidence=[good])])
    r = verify_evidence(g, code)
    assert [n.id for n in r.graph.nodes] == ["COMP-0001", "COMP-0003"]
    assert r.graph.nodes[0].evidence == [good]
    assert [e.id for e in r.graph.edges] == ["EDGE-0003"]
    assert set(r.rejected) == {"COMP-0002", "EDGE-0001", "EDGE-0002"}
    assert_all_evidence_valid(r.graph, code)


def test_dead_code_with_inventory_kept_low(code):
    g = Graph(nodes=[node(1, [Evidence(file="a.py", line_start=1, symbol="zzz")], "dead_code", "DEAD", ["inv1"]),
                     node(2, [Evidence(file="gone.py")], "config", "CFG", ["inv2"]),
                     node(3, [Evidence(file="gone.py")], "dead_code", "DEAD")])
    r = verify_evidence(g, code)
    assert sorted(r.demoted) == ["CFG-0002", "DEAD-0001"] and r.rejected == ["DEAD-0003"]
    assert r.graph.node("DEAD-0001").confidence == "low"
    assert_all_evidence_valid(r.graph, code)


def test_gate_raises(code):
    g = Graph(nodes=[node(1, [Evidence(file="x.py")])])
    with pytest.raises(EvidenceError):
        assert_all_evidence_valid(g, code)
