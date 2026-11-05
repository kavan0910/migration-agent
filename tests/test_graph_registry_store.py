import itertools
import json
import random

import pytest

from migration_agent.graph import (Edge, Evidence, Graph, GraphStoreError, Node, Registry, diff_graphs,
                                   generate_json_schema, load_latest, load_snapshot, save_snapshot)

KEYS = [("component", "m", "A"), ("component", "m", "B"), ("endpoint", "api", "GET /x"),
        ("data_entity", "db", "User"), ("component", "n", "C")]


def test_prefixes_and_counters():
    r = Registry()
    ids = r.allocate_nodes(KEYS)
    assert ids[KEYS[0]] == "COMP-0001" and ids[KEYS[1]] == "COMP-0002"
    assert ids[KEYS[2]] == "EP-0001" and ids[KEYS[3]] == "ENT-0001" and ids[KEYS[4]] == "COMP-0003"


def test_deterministic_regardless_of_order():
    results = set()
    for _ in range(10):
        ks = KEYS[:]
        random.shuffle(ks)
        results.add(json.dumps(sorted(Registry().allocate_nodes(ks).items())))
    assert len(results) == 1


def test_identity_ignores_name_collisions_across_type():
    r = Registry()
    assert r.node_id("component", "m", "X") != r.node_id("config", "m", "X")
    assert r.node_id("component", "m", "X") == r.node_id("component", "m", "X")


def test_stability_across_runs_and_never_reused(tmp_path):
    p = tmp_path / "registry.json"
    r = Registry(p)
    first = r.allocate_nodes(KEYS[:2])
    r.save()
    r2 = Registry(p)
    # new key sorted before existing ones must not shift IDs
    again = r2.allocate_nodes([("component", "a", "Z")] + KEYS[:2])
    assert {k: again[k] for k in KEYS[:2]} == first
    assert again[("component", "a", "Z")] == "COMP-0003"
    r2.save()
    # a key that vanishes from later graphs keeps its id reserved
    r3 = Registry(p)
    assert r3.node_id("component", "q", "New") == "COMP-0004"
    assert r3.node_id(*KEYS[0]) == first[KEYS[0]]


def test_unknown_type_and_corrupt(tmp_path):
    with pytest.raises(ValueError):
        Registry().node_id("nope", "m", "s")
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"counters": {}, "entries": {"a": "COMP-0001", "b": "COMP-0001"}}))
    with pytest.raises(ValueError):
        Registry(p)


def test_edge_ids():
    r = Registry()
    ids = r.allocate_edges([("COMP-0002", "calls", "COMP-0001"), ("COMP-0001", "calls", "COMP-0002")])
    assert ids[("COMP-0001", "calls", "COMP-0002")] == "EDGE-0001"


def make_graph(order=None):
    r = Registry()
    ids = r.allocate_nodes(KEYS)
    nodes = [Node(id=ids[k], type=k[0], name=k[2], module=k[1], qualified_symbol=k[2],
                  evidence=[Evidence(file="a.py", line_start=1, quote="x")]) for k in KEYS]
    eid = r.allocate_edges([(ids[KEYS[0]], "calls", ids[KEYS[1]])])
    edges = [Edge(id=v, source=k[0], target=k[2], type=k[1]) for k, v in eid.items()]
    if order:
        nodes = order(nodes)
    return Graph(nodes=nodes, edges=edges)


def test_snapshots_and_latest(tmp_path):
    gd = tmp_path / "graph"
    assert load_latest(gd) is None
    p1 = save_snapshot(gd, make_graph())
    p2 = save_snapshot(gd, make_graph(lambda n: n[::-1]))
    assert p1.name == "v001.json" and p2.name == "v002.json"
    assert not (gd / "latest.json").is_symlink()
    g = load_latest(gd)
    assert g.version == 2 and [n.id for n in g.nodes] == sorted(n.id for n in g.nodes)
    # insertion order does not change bytes (besides version)
    assert p1.read_text().replace('"version": 1', '"version": 2') == p2.read_text()
    assert load_snapshot(gd, 1).version == 1
    assert '"confidence"' in p1.read_text()
    with pytest.raises(GraphStoreError):
        load_snapshot(gd, 9)


def test_invalid_on_read(tmp_path):
    gd = tmp_path / "graph"
    p = save_snapshot(gd, make_graph())
    d = json.loads(p.read_text())
    d["edges"][0]["target"] = "COMP-9999"
    p.write_text(json.dumps(d))
    with pytest.raises(GraphStoreError):
        load_latest(gd)
    p.write_text("not json")
    with pytest.raises(GraphStoreError):
        load_latest(gd)
    (gd / "latest.json").write_text(json.dumps({"path": "../../etc/passwd"}))
    with pytest.raises(GraphStoreError):
        load_latest(gd)


def test_invalid_on_write(tmp_path):
    g = make_graph()
    g.edges[0].target = "COMP-9999"  # mutate past validation
    with pytest.raises(Exception):
        save_snapshot(tmp_path / "graph", g)
    assert not (tmp_path / "graph" / "latest.json").exists()


def test_schema(tmp_path):
    s = generate_json_schema(tmp_path / "graph")
    assert (tmp_path / "graph" / "schema.json").exists()
    assert "nodes" in s["properties"]


def test_diff():
    old = make_graph()
    assert diff_graphs(old, make_graph()).empty
    new = old.model_copy(deep=True)
    changed = new.nodes[0]
    changed.quirk = True
    removed = new.nodes.pop()
    new.edges = []
    new.nodes.append(Node(id="JOB-0001", type="job", name="j"))
    d = diff_graphs(old, new)
    assert d.added_nodes == ["JOB-0001"] and d.removed_nodes == [removed.id]
    assert d.changed_nodes == [changed.id] and d.removed_edges == ["EDGE-0001"]
    assert diff_graphs(None, old).added_nodes == sorted(n.id for n in old.nodes)
