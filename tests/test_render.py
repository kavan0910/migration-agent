import re

from migration_agent.graph.models import Edge, Evidence, Graph, Node
from migration_agent.render import SECTIONS, render_changelog, render_understanding


def ev(f="src/a.py", s=3, e=9, sym="foo", q="def foo():"):
    return Evidence(file=f, line_start=s, line_end=e, symbol=sym, quote=q)


def graph(version=1):
    return Graph(version=version, nodes=[
        Node(id="COMP-0001", type="component", name="OrderService", module="src", responsibility="Handles orders",
             evidence=[ev(), ev(s=20, e=22, sym="bar")]),
        Node(id="COMP-0002", type="component", name="AuthMiddleware", module="src/auth", responsibility="auth checks",
             evidence=[Evidence(file="src/auth.py")]),
        Node(id="COMP-0003", type="component", name="TestOrders", module="tests", evidence=[ev("tests/test_orders.py")]),
        Node(id="EP-0001", type="endpoint", name="POST /orders", evidence=[ev()]),
        Node(id="ENT-0001", type="data_entity", name="orders", evidence=[ev(), ev(s=30, e=31, sym="x")]),
        Node(id="EXT-0001", type="external_dependency", name="stripe", evidence=[ev(), ev(s=5, e=5, sym="y")]),
        Node(id="RULE-0001", type="business_rule", name="Orders expire after 72h", quirk=True,
             evidence=[ev(), ev(s=40, e=41, sym="z")]),
        Node(id="RULE-0002", type="business_rule", name="Discount applied twice", suspected_bug=True, evidence=[ev()]),
        Node(id="JOB-0001", type="job", name="nightly", evidence=[ev(), ev(s=7, e=8, sym="j")]),
        Node(id="CFG-0001", type="config", name="DB_URL", evidence=[ev(), ev(s=8, e=8, sym="k")]),
        Node(id="INT-0001", type="integration", name="Payments API", evidence=[ev(), ev(s=9, e=9, sym="m")]),
        Node(id="DEAD-0001", type="dead_code", name="legacy_export", evidence=[ev(), ev(s=9, e=9, sym="n")]),
    ], edges=[
        Edge(id="EDGE-0001", source="COMP-0001", target="EXT-0001", type="depends_on", evidence=[ev()]),
        Edge(id="EDGE-0002", source="COMP-0001", target="ENT-0001", type="writes"),
        Edge(id="EDGE-0003", source="COMP-0001", target="INT-0001", type="calls", evidence=[ev()]),
    ])


def render(g=None, **kw):
    return render_understanding(g or graph(), {"name": "Shop", "source": "/x", "purpose": "Sells things",
                                               **kw.pop("meta", {})}, {"source_file": 3}, **kw)


def test_all_15_headings():
    md = render()
    heads = re.findall(r"^## (\d+)\. (.+)$", md, re.M)
    assert [int(n) for n, _ in heads] == list(range(1, 16))
    assert [t for _, t in heads] == SECTIONS


def test_anchors_and_evidence():
    md = render()
    for i in ["COMP-0001", "EP-0001", "ENT-0001", "EXT-0001", "RULE-0001", "JOB-0001", "CFG-0001", "INT-0001", "DEAD-0001", "EDGE-0001"]:
        assert f'<a id="{i}"></a>' in md
    assert "`src/a.py:3-9` (foo)" in md
    assert "Confidence: high" in md
    assert "Responsibility: Handles orders" in md
    assert "`src/auth.py`" in md  # no line


def test_quirk_and_bug_sections():
    md = render()
    assert "preserved quirk" in md
    bugs = md.split("### Found bugs & suspicious behaviour")[1].split("### Preserved quirks")[0]
    assert "RULE-0002" in bugs and "RULE-0001" not in bugs
    assert "RULE-0001" in md.split("### Preserved quirks")[1]


def test_blind_spots():
    md = render(meta={"fallback_parsed_files": ["legacy/x.cob"]})
    blind = md.split("## 13.")[1].split("## 14.")[0]
    assert "COMP-0002" in blind and "confidence low" in blind
    assert "[COMP-0001]" not in blind and "EDGE-0002" in blind
    assert "`legacy/x.cob`" in blind


def test_cross_cutting_tests_and_relations():
    md = render()
    assert "AuthMiddleware" in md.split("## 10.")[1].split("## 11.")[0]
    assert "TestOrders" in md.split("## 11.")[1].split("## 12.")[0]
    assert "depends_on [EXT-0001](#EXT-0001)" in md


def test_empty_graph():
    md = render_understanding(Graph(), {}, {})
    assert len(re.findall(r"^## \d+\. ", md, re.M)) == 15
    assert "graph is empty" in md
    assert "No endpoints recorded." in md
    assert "No business rules recorded." in md
    assert "No suspected bugs were found." in md
    assert "No blind spots" in md
    assert "No coverage report provided." in md
    assert "Changelog" not in md


def test_open_questions_and_coverage():
    md = render(meta={"open_questions": ["Why 72h?"], "assumptions": [{"id": "A1", "text": "UTC"}]},
                coverage={"mapped": 3, "unmapped": 0, "excluded": 1})
    assert "- Why 72h?" in md and "- A1: UTC" in md
    assert "- mapped: 3" in md and "- source_file: 3" in md


def test_changelog():
    old = graph(1)
    new = graph(2)
    new.nodes = [n for n in new.nodes if n.id != "JOB-0001"]
    new.nodes[0] = new.nodes[0].model_copy(update={"responsibility": "Handles orders v2"})
    new.nodes.append(Node(id="EP-0002", type="endpoint", name="GET /orders", evidence=[ev()]))
    md = render(new, previous=old, meta={"change_reasons": {"EP-0002": "found by gap filler"}})
    cl = md.split("## Changelog")[1]
    assert "v1 to v2" in cl
    assert "[EP-0002](#EP-0002) GET /orders" in cl and "found by gap filler" in cl
    assert "JOB-0001 nightly" in cl.split("### Removed")[1]
    assert "COMP-0001" in cl.split("### Changed")[1] and "responsibility" in cl
    assert "No changes" in render_changelog(old, old)
    assert "Added" in render_changelog(None, old)


def test_deterministic_and_order_independent():
    g = graph()
    g2 = Graph(version=1, nodes=list(reversed(g.nodes)), edges=list(reversed(g.edges)))
    assert render(g) == render(g) == render(g2)


def test_facts_only_graph():
    g = Graph(nodes=[
        Node(id="COMP-0001", type="component", name="a.py", module=".", responsibility="(facts only: not yet analysed)",
             evidence=[Evidence(file="a.py")]),
        Node(id="EP-0001", type="endpoint", name="GET /x", evidence=[Evidence(file="a.py", line_start=2, line_end=2, symbol="GET /x")]),
    ])
    md = render_understanding(g, {"name": "p"}, {"source_file": 1, "route": 1})
    assert 'id="COMP-0001"' in md and "`a.py:2` (GET /x)" in md and "facts only" in md
    assert len(re.findall(r"^## \d+\. ", md, re.M)) == 15
