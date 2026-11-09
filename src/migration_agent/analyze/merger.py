"""Order-independent graph merger and chunk checkpointing (B-04)."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from ..graph.models import Edge, Evidence, Graph, Node
from ..graph.registry import Registry
from .patch import ChunkPatch, EdgePatch, NodeRef, UnresolvedRef

Key = tuple[str, str, str]


class MergeResult(BaseModel):
    graph: Graph
    dropped_edges: list[EdgePatch] = Field(default_factory=list)
    unresolved: list[UnresolvedRef] = Field(default_factory=list)


def _ev_sort(e: Evidence) -> tuple:
    return (e.file, e.line_start or 0, e.line_end or 0, e.symbol or "", e.quote or "")


def _union_ev(items: list[Evidence]) -> list[Evidence]:
    seen: dict[tuple, Evidence] = {}
    for e in items:
        seen.setdefault(_ev_sort(e), e)
    return [seen[k] for k in sorted(seen)]


def _dump(m: BaseModel) -> str:
    return json.dumps(m.model_dump(mode="json"), sort_keys=True)


def merge_patches(patches: list[ChunkPatch], registry: Registry, base: Graph | None = None) -> MergeResult:
    acc: dict[Key, dict] = {}
    edge_acc: dict[tuple[Key, str, Key], list[Evidence]] = {}
    unresolved: dict[str, UnresolvedRef] = {}

    def add_node(key: Key, name: str, resp: str, ev, refs, quirk, bug) -> None:
        a = acc.setdefault(key, {"names": set(), "resp": [], "ev": [], "refs": set(), "quirk": False, "bug": False})
        if name:
            a["names"].add(name)
        if resp.strip():
            a["resp"].append(resp)
        a["ev"].extend(ev)
        a["refs"].update(refs)
        a["quirk"] |= quirk
        a["bug"] |= bug

    if base is not None:
        by_id: dict[str, Key] = {}
        for n in base.nodes:
            k = (n.type, n.module, n.qualified_symbol)
            by_id[n.id] = k
            add_node(k, n.name, n.responsibility, n.evidence, n.inventory_refs, n.quirk, n.suspected_bug)
        for e in base.edges:
            edge_acc.setdefault((by_id[e.source], e.type, by_id[e.target]), []).extend(e.evidence)
            registry.adopt_edge(e.source, e.type, e.target, e.id)

    for p in patches:
        for n in p.nodes:
            add_node(n.key(), n.name, n.responsibility, n.evidence, n.inventory_refs, n.quirk, n.suspected_bug)
        for e in p.edges:
            edge_acc.setdefault((e.source.key(), e.type, e.target.key()), []).extend(e.evidence)
        for u in p.unresolved:
            unresolved.setdefault(_dump(u), u)

    ids = registry.allocate_nodes(sorted(acc))
    nodes: list[Node] = []
    for k in sorted(acc):
        a = acc[k]
        resp = min(a["resp"], key=lambda r: (-len(r), r)) if a["resp"] else ""
        name = min(a["names"], key=lambda s: (-len(s), s)) if a["names"] else (k[2] or k[1] or k[0])
        nodes.append(Node(
            id=ids[k], type=k[0], name=name, module=k[1], qualified_symbol=k[2],
            responsibility=resp, evidence=_union_ev(a["ev"]), inventory_refs=sorted(a["refs"]),
            quirk=a["quirk"], suspected_bug=a["bug"],
        ))

    good: list[tuple[Key, str, Key]] = []
    dropped: dict[str, EdgePatch] = {}
    for ekey in sorted(edge_acc):
        s, t, ty = ekey[0], ekey[2], ekey[1]
        if s in acc and t in acc:
            good.append(ekey)
        else:
            dropped.setdefault(_dump(ep := EdgePatch(
                source=NodeRef(type=s[0], module=s[1], qualified_symbol=s[2]),
                target=NodeRef(type=t[0], module=t[1], qualified_symbol=t[2]),
                type=ty, evidence=_union_ev(edge_acc[ekey]))), ep)
    eids = registry.allocate_edges([(ids[s], ty, ids[t]) for s, ty, t in good])
    edges = [
        Edge(id=eids[(ids[s], ty, ids[t])], source=ids[s], target=ids[t], type=ty,
             evidence=_union_ev(edge_acc[(s, ty, t)]))
        for s, ty, t in good
    ]
    graph = Graph(version=base.version if base else 0,
                  nodes=sorted(nodes, key=lambda n: n.id), edges=sorted(edges, key=lambda e: e.id))
    return MergeResult(graph=graph, dropped_edges=[dropped[k] for k in sorted(dropped)],
                       unresolved=[unresolved[k] for k in sorted(unresolved)])


class Checkpoint:
    """Per-chunk patch persistence: <dir>/chunks/<chunk_id>.json, atomic writes."""

    def __init__(self, dir: str | Path):
        self.dir = Path(dir)
        self.chunks = self.dir / "chunks"

    def _path(self, chunk_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", chunk_id) or chunk_id in (".", ".."):
            raise ValueError(f"unsafe chunk id {chunk_id!r}")
        return self.chunks / f"{chunk_id}.json"

    def save_patch(self, patch: ChunkPatch) -> Path:
        p = self._path(patch.chunk_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            f.write(patch.model_dump_json())
        os.replace(tmp, p)
        return p

    def completed_chunk_ids(self) -> set[str]:
        if not self.chunks.is_dir():
            return set()
        return {p.stem for p in self.chunks.glob("*.json") if not p.name.startswith(".")}

    def load_patches(self, ids: "set[str] | list[str] | None" = None) -> list[ChunkPatch]:
        """Load saved patches in sorted-id order (optionally only `ids`; unknown ids are skipped)."""
        have = self.completed_chunk_ids()
        want = sorted(have if ids is None else have & set(ids))
        return [ChunkPatch.model_validate_json((self.chunks / f"{c}.json").read_text()) for c in want]

    def load_patch(self, chunk_id: str) -> ChunkPatch | None:
        p = self._path(chunk_id)
        return ChunkPatch.model_validate_json(p.read_text()) if p.exists() else None

    def delete_patch(self, chunk_id: str) -> None:
        self._path(chunk_id).unlink(missing_ok=True)

    # -- cached results of whole-graph steps (cross_ref, gap_fill): checkpoint/extra/<name>.json {"sig", ...}
    def save_extra(self, name: str, sig: str, payload: dict) -> None:
        p = self.dir / "extra" / f"{name}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            json.dump({"sig": sig, **payload}, f)
        os.replace(tmp, p)

    def load_extra(self, name: str, sig: str) -> dict | None:
        try:
            d = json.loads((self.dir / "extra" / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None
        return d if isinstance(d, dict) and d.get("sig") == sig else None

    # -- failures: checkpoint/failures.json {chunk_id: {task, error_class, error, ts}}
    @property
    def failures_path(self) -> Path:
        return self.dir / "failures.json"

    def load_failures(self) -> dict[str, dict]:
        try:
            d = json.loads(self.failures_path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_failures(self, failures: dict[str, dict]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            json.dump(failures, f, indent=2, sort_keys=True)
        os.replace(tmp, self.failures_path)


def dedupe_fact_endpoints(graph: Graph) -> Graph:
    """Drop facts-only endpoint stubs ("GET /@7": router-relative path + line) when the model produced a
    full-path endpoint for the same file+method on (near) the same line; the stub's inventory refs move
    to the surviving node and edges are re-pointed."""
    def split(n: Node):
        method, _, path = n.qualified_symbol.partition(" ")
        return method, path

    is_stub = lambda n: n.responsibility.startswith("(facts only")
    stubs = [n for n in graph.nodes if n.type == "endpoint" and is_stub(n)]
    full = [n for n in graph.nodes if n.type == "endpoint" and not is_stub(n)]
    full_idx: dict[tuple[str, str], list[Node]] = {}
    for f in full:
        full_idx.setdefault((f.module, split(f)[0]), []).append(f)
    remap: dict[str, str] = {}
    for st in stubs:
        method, spath = split(st)
        spath = spath.rsplit("@", 1)[0]
        sline = next((e.line_start for e in st.evidence if e.line_start), None)
        cands = [f for f in full_idx.get((st.module, method), ())
                 if (split(f)[1].endswith(spath) if spath != "/" else True)]
        near = [f for f in cands if sline is not None and any(
            e.line_start is not None and abs(e.line_start - sline) <= 3 for e in f.evidence)]
        pick = near[0] if len(near) == 1 else (cands[0] if len(cands) == 1 else None)
        if pick is not None:
            remap[st.id] = pick.id
    if not remap:
        return graph
    by_id = {n.id: n for n in graph.nodes}
    absorbed: dict[str, list[str]] = {}
    for old, new in remap.items():
        absorbed.setdefault(new, []).append(old)
    nodes = []
    for n in graph.nodes:
        if n.id in remap:
            continue
        extra = [r for old in absorbed.get(n.id, ()) for r in by_id[old].inventory_refs]
        nodes.append(n.model_copy(update={"inventory_refs": sorted(set(n.inventory_refs) | set(extra))}) if extra else n)
    seen, edges = set(), []
    for e in graph.edges:
        e2 = e.model_copy(update={"source": remap.get(e.source, e.source), "target": remap.get(e.target, e.target)})
        k = (e2.source, e2.type, e2.target)
        if k not in seen and e2.source != e2.target:
            seen.add(k)
            edges.append(e2)
    return Graph(version=graph.version, nodes=nodes, edges=edges)


def collapse_entity_components(graph: Graph) -> Graph:
    """A file that defines a data entity is that entity, not a separate component: merge the file's
    component node into the (first) entity node, keeping its inventory refs, and re-point edges
    (calls touching an entity become depends_on; duplicates and self-loops are dropped)."""
    ent_by_mod: dict[str, Node] = {}
    for n in sorted(graph.nodes, key=lambda x: x.id):
        if n.type == "data_entity" and n.module:
            ent_by_mod.setdefault(n.module, n)
    remap = {n.id: ent_by_mod[n.module].id for n in graph.nodes
             if n.type == "component" and n.module in ent_by_mod}
    if not remap:
        return graph
    by_id = {n.id: n for n in graph.nodes}
    absorbed: dict[str, list[str]] = {}
    for old, new in remap.items():
        absorbed.setdefault(new, []).append(old)
    nodes = []
    for n in graph.nodes:
        if n.id in remap:
            continue
        extra = [r for old in absorbed.get(n.id, ()) for r in by_id[old].inventory_refs]
        nodes.append(n.model_copy(update={"inventory_refs": sorted(set(n.inventory_refs) | set(extra))}) if extra else n)
    ent_ids = {n.id for n in nodes if n.type == "data_entity"}
    seen, edges = {}, []
    for e in graph.edges:
        src, tgt = remap.get(e.source, e.source), remap.get(e.target, e.target)
        typ = "depends_on" if e.type == "calls" and (src in ent_ids or tgt in ent_ids) else e.type
        if src == tgt:
            continue
        k = (src, typ, tgt)
        if k in seen:
            continue
        seen[k] = True
        edges.append(e.model_copy(update={"source": src, "target": tgt, "type": typ}))
    return Graph(version=graph.version, nodes=nodes, edges=edges)
