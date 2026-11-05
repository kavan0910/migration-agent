import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from migration_agent.graph.models import ID_PREFIX, Edge, Evidence, Graph, Node
from migration_agent.viz.graph_html import render_graph_html, write_graph_html

STRONG = [
    Evidence(file="src/routes/users.js", line_start=12, line_end=20, symbol="getUser", quote="router.get('/users/:id', ...)"),
    Evidence(file="src/models/user.js", line_start=3, quote="module.exports = User"),
]


def _n(type_, num, name, **kw):
    return Node(id=f"{ID_PREFIX[type_]}-{num:04d}", type=type_, name=name, **kw)


def sample_graph(extra_nodes: int = 0) -> Graph:
    """~30 node express-crud-like graph."""
    nodes, edges = [], []
    nodes.append(_n("component", 1, "app.js", module="src", responsibility="Express bootstrap and middleware wiring", evidence=STRONG))
    resources = ["users", "orders", "products", "invoices", "sessions"]
    n = 2
    for r in resources:
        ctrl = _n("component", n, f"{r}Controller", module="src/controllers", responsibility=f"CRUD handlers for {r}", evidence=STRONG)
        ent = _n("data_entity", n + 1, r.capitalize(), module="src/models", responsibility=f"{r} table", evidence=STRONG[:1])
        nodes += [ctrl, ent]
        eps = []
        for j, (verb, path) in enumerate([("GET", f"/{r}"), ("POST", f"/{r}"), ("DELETE", f"/{r}/:id")]):
            ep = _n("endpoint", n + 2 + j, f"{verb} {path}", evidence=STRONG[:1] if j else STRONG)
            eps.append(ep)
            nodes.append(ep)
        for ep in eps:
            edges.append(("calls", ep.id, ctrl.id))
        edges.append(("reads", ctrl.id, ent.id))
        edges.append(("writes", ctrl.id, ent.id))
        edges.append(("depends_on", "COMP-0001", ctrl.id))
        n += 5
    nodes.append(_n("external_dependency", 100, "express", evidence=STRONG))
    nodes.append(_n("external_dependency", 101, "jsonwebtoken"))
    nodes.append(_n("config", 102, "config/default.json", evidence=STRONG[:1]))
    nodes.append(_n("business_rule", 103, "Orders over 1000 need approval", responsibility="Approval threshold", quirk=True, evidence=STRONG))
    nodes.append(_n("business_rule", 104, "Invoice rounding", suspected_bug=True, evidence=STRONG[:1]))
    nodes.append(_n("job", 105, "nightlyInvoiceJob", evidence=STRONG))
    nodes.append(_n("integration", 106, "Stripe webhook", evidence=STRONG[:1]))
    nodes.append(_n("dead_code", 107, "legacyExport"))
    edges += [
        ("depends_on", "COMP-0001", "EXT-0100"), ("depends_on", "COMP-0001", "EXT-0101"),
        ("configures", "CFG-0102", "COMP-0001"), ("implements", "COMP-0003", "RULE-0103"),
        ("implements", "COMP-0018", "RULE-0104"), ("calls", "JOB-0105", "COMP-0018"),
        ("writes", "JOB-0105", "ENT-0019"), ("consumes", "COMP-0013", "INT-0106"),
        ("publishes", "COMP-0008", "INT-0106"), ("depends_on", "COMP-0001", "JOB-0105"),
    ]
    for i in range(extra_nodes):
        nodes.append(_n("component", 1000 + i, f"gen{i}", evidence=STRONG[:1]))
    ids = {x.id for x in nodes}
    es = [Edge(id=f"EDGE-{i:04d}", source=s, target=t, type=ty, evidence=STRONG[:1])
          for i, (ty, s, t) in enumerate(edges) if s in ids and t in ids]
    for i in range(extra_nodes):
        es.append(Edge(id=f"EDGE-{5000 + i:04d}", source=f"COMP-{1000 + i:04d}", target=f"COMP-{1000 + max(0, i // 3 - 1):04d}" if i // 3 - 1 != i else "COMP-0001", type="calls"))
    return Graph(nodes=nodes, edges=es)


def _embedded(html: str) -> dict:
    m = re.search(r'<script id="graph-data" type="application/json">(.*?)</script>', html, re.S)
    assert m
    return json.loads(m.group(1))


def test_self_contained_and_embeds_ids():
    g = sample_graph()
    html = render_graph_html(g, title="Demo")
    assert not re.search(r'(?:src|href)\s*=\s*["\']?\s*(?:https?:)?//', html, re.I)
    assert "@import" not in html and "<link" not in html
    for n in g.nodes:
        assert n.id in html
    data = _embedded(html)
    assert {n["id"] for n in data["nodes"]} == {n.id for n in g.nodes}
    assert len(data["edges"]) == len(g.edges)
    assert "lavish-theme" in html and 'data-theme="dark"' in html


def test_escapes_malicious_content():
    evil = "</script><img src=x onerror=alert(1)>"
    g = Graph(nodes=[
        _n("component", 1, evil, responsibility="<b>x</b></script>", evidence=[Evidence(file="a.js", line_start=1, quote=evil)]),
    ])
    html = render_graph_html(g, title=evil)
    assert evil not in html
    assert "<img src=x" not in html
    assert html.lower().count("</script>") == html.lower().count("<script")
    assert _embedded(html)["nodes"][0]["name"] == evil  # round-trips losslessly


def test_empty_graph():
    html = render_graph_html(Graph())
    assert "No nodes in this graph yet" in html
    assert _embedded(html)["nodes"] == []


def test_diff_mode_marks_added_changed_removed():
    old = sample_graph()
    new = Graph(nodes=list(old.nodes), edges=list(old.edges))
    removed = new.nodes.pop(new.nodes.index(new.node("DEAD-0107")))
    changed = new.node("COMP-0001").model_copy(update={"responsibility": "changed!"})
    new.nodes[new.nodes.index(new.node("COMP-0001"))] = changed
    new.nodes.append(_n("job", 200, "brandNewJob"))
    html = render_graph_html(new, previous=old)
    data = _embedded(html)
    st = {n["id"]: n["status"] for n in data["nodes"]}
    assert st["JOB-0200"] == "added" and st["COMP-0001"] == "changed" and st[removed.id] == "removed"
    assert data["diff"] is True
    assert "Changes vs previous" in html
    assert _embedded(render_graph_html(old))["diff"] is False


def test_write_graph_html(tmp_path):
    p = write_graph_html(tmp_path / "sub" / "g.html", sample_graph())
    assert p.read_text(encoding="utf-8").startswith("<!doctype html>")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_embedded_js_parses(tmp_path):
    html = render_graph_html(sample_graph(extra_nodes=2000))
    assert len(_embedded(html)["nodes"]) > 2000
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    f = tmp_path / "s.js"
    f.write_text(scripts[-1], encoding="utf-8")
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
