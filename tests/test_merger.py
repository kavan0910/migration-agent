import random

from migration_agent.analyze.merger import Checkpoint, merge_patches
from migration_agent.analyze.patch import ChunkPatch, EdgePatch, NodePatch, NodeRef, UnresolvedRef
from migration_agent.graph.models import Evidence
from migration_agent.graph.registry import Registry


def ev(f="a.py", s=1, q=None):
    return Evidence(file=f, line_start=s, quote=q)


def np_(sym, **kw):
    return NodePatch(type="component", module="m", qualified_symbol=sym, name=sym, **kw)


def ref(sym):
    return NodeRef(type="component", module="m", qualified_symbol=sym)


def patches():
    return [
        ChunkPatch(chunk_id="CH-0001", nodes=[np_("A", responsibility="short", evidence=[ev(s=1)], inventory_refs=["i2"]),
                                              np_("B", quirk=True)],
                   edges=[EdgePatch(source=ref("A"), target=ref("B"), type="calls", evidence=[ev(s=3)])]),
        ChunkPatch(chunk_id="CH-0002", nodes=[np_("A", responsibility="a longer text", evidence=[ev(s=1), ev(s=2)],
                                                  inventory_refs=["i1"], suspected_bug=True), np_("C")],
                   edges=[EdgePatch(source=ref("A"), target=ref("B"), type="calls", evidence=[ev(s=3), ev(s=4)]),
                          EdgePatch(source=ref("A"), target=ref("ZZ"), type="calls")],
                   unresolved=[UnresolvedRef(from_node=ref("C"), name="foo")]),
        ChunkPatch(chunk_id="CH-0003", nodes=[np_("A", responsibility="b longer text")],
                   unresolved=[UnresolvedRef(from_node=ref("C"), name="foo")]),
    ]


def test_merge_semantics():
    r = merge_patches(patches(), Registry())
    a = next(n for n in r.graph.nodes if n.qualified_symbol == "A")
    assert a.responsibility == "a longer text"
    assert [e.line_start for e in a.evidence] == [1, 2]
    assert a.inventory_refs == ["i1", "i2"] and a.suspected_bug
    assert next(n for n in r.graph.nodes if n.qualified_symbol == "B").quirk
    assert len(r.graph.edges) == 1 and len(r.graph.edges[0].evidence) == 2
    assert len(r.dropped_edges) == 1 and r.dropped_edges[0].target.qualified_symbol == "ZZ"
    assert len(r.unresolved) == 1


def test_shuffle_determinism():
    base = merge_patches(patches(), Registry()).model_dump_json()
    ps = patches()
    for seed in range(20):
        random.Random(seed).shuffle(ps)
        assert merge_patches(ps, Registry()).model_dump_json() == base


def test_id_stability(tmp_path):
    reg = Registry(tmp_path / "r.json")
    g1 = merge_patches(patches(), reg).graph
    reg.save()
    more = patches() + [ChunkPatch(chunk_id="CH-0004", nodes=[np_("0first")])]
    g2 = merge_patches(more, Registry(tmp_path / "r.json")).graph
    ids1 = {n.qualified_symbol: n.id for n in g1.nodes}
    ids2 = {n.qualified_symbol: n.id for n in g2.nodes}
    assert all(ids2[k] == v for k, v in ids1.items())
    assert ids2["0first"] not in ids1.values()


def test_base_merge():
    reg = Registry()
    g1 = merge_patches(patches()[:1], reg).graph
    g2 = merge_patches(patches()[1:], reg, base=g1).graph
    assert g2.model_dump() == merge_patches(patches(), Registry()).graph.model_dump()


def test_checkpoint_resume(tmp_path):
    cp = Checkpoint(tmp_path)
    assert cp.completed_chunk_ids() == set()
    ps = patches()
    cp.save_patch(ps[0]); cp.save_patch(ps[1])
    cp2 = Checkpoint(tmp_path)
    assert cp2.completed_chunk_ids() == {"CH-0001", "CH-0002"}
    cp2.save_patch(ps[2])
    loaded = cp2.load_patches()
    assert [p.chunk_id for p in loaded] == ["CH-0001", "CH-0002", "CH-0003"]
    assert merge_patches(loaded, Registry()).model_dump_json() == merge_patches(ps, Registry()).model_dump_json()
    assert not list((tmp_path / "chunks").glob(".tmp-*"))
