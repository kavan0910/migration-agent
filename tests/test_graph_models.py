import pytest
from pydantic import ValidationError

from migration_agent.graph.models import Edge, Evidence, Graph, Node

STRONG = Evidence(file="a.py", line_start=1, line_end=3, symbol="f", quote="def f")


def node(i="COMP-0001", t="component", **kw):
    return Node(id=i, type=t, name="n", **kw)


def test_confidence_derived():
    assert node().confidence == "none"
    assert node(evidence=[Evidence(file="a.py")]).confidence == "low"
    assert node(evidence=[STRONG]).confidence == "medium"
    assert node(evidence=[Evidence(file="a.py"), Evidence(file="b.py")]).confidence == "medium"
    assert node(evidence=[STRONG, STRONG]).confidence == "high"


def test_confidence_not_settable():
    with pytest.raises(ValidationError):
        Node(id="COMP-0001", type="component", name="n", confidence="high")


def test_validation_errors():
    with pytest.raises(ValidationError):
        node(i="EP-0001")  # wrong prefix
    with pytest.raises(ValidationError):
        Node(id="COMP-0001", type="bogus", name="n")
    with pytest.raises(ValidationError):
        Evidence(file="a", line_start=5, line_end=2)
    with pytest.raises(ValidationError):
        Evidence(file="a", line_end=2)
    with pytest.raises(ValidationError):
        Edge(id="X-1", source="a", target="b", type="calls")
    with pytest.raises(ValidationError):
        Edge(id="EDGE-0001", source="a", target="b", type="eats")


def test_graph_integrity():
    a, b = node("COMP-0001"), node("COMP-0002")
    e = Edge(id="EDGE-0001", source=a.id, target=b.id, type="calls")
    Graph(nodes=[a, b], edges=[e])
    with pytest.raises(ValidationError, match="duplicate node"):
        Graph(nodes=[a, a])
    with pytest.raises(ValidationError, match="unknown node"):
        Graph(nodes=[a], edges=[e])
    with pytest.raises(ValidationError, match="duplicate edge"):
        Graph(nodes=[a, b], edges=[e, e])


def test_all_node_types_have_prefix():
    from migration_agent.graph.models import ID_PREFIX
    for t, p in ID_PREFIX.items():
        assert node(f"{p}-0001", t).type == t
