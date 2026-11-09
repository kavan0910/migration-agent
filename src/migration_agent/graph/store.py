"""Graph snapshots: graph/snapshots/vNNN.json + graph/latest.json pointer (no symlinks)."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .models import Graph


COMPACT_ABOVE = 5000  # nodes+edges; larger snapshots are written without indentation (size/speed)


class GraphStoreError(Exception):
    pass


class GraphDiff(BaseModel):
    added_nodes: list[str] = []
    removed_nodes: list[str] = []
    changed_nodes: list[str] = []
    added_edges: list[str] = []
    removed_edges: list[str] = []
    changed_edges: list[str] = []

    @property
    def empty(self) -> bool:
        return not any(self.model_dump().values())


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _canonical(graph: Graph, version: int) -> Graph:
    return Graph(
        version=version,
        nodes=sorted(graph.nodes, key=lambda n: n.id),
        edges=sorted(graph.edges, key=lambda e: e.id),
    )


def _dump(graph: Graph) -> str:
    d = graph.model_dump(mode="json")
    for raw, n in zip(d["nodes"], graph.nodes):
        raw["confidence"] = n.confidence
    for raw, e in zip(d["edges"], graph.edges):
        raw["confidence"] = e.confidence
    big = len(d["nodes"]) + len(d["edges"]) > COMPACT_ABOVE
    return (json.dumps(d, separators=(",", ":")) if big else json.dumps(d, indent=2)) + "\n"


def parse_graph(text: str) -> Graph:
    try:
        d = json.loads(text)
        for item in d.get("nodes", []) + d.get("edges", []):
            item.pop("confidence", None)  # derived, never trusted from disk
        return Graph.model_validate(d)
    except (ValueError, ValidationError, AttributeError, TypeError) as e:
        raise GraphStoreError(f"invalid graph: {e}") from e


def _versions(snap_dir: Path) -> list[int]:
    if not snap_dir.is_dir():
        return []
    return sorted(int(m.group(1)) for p in snap_dir.iterdir() if (m := re.fullmatch(r"v(\d+)\.json", p.name)))


def snapshot_path(graph_dir: Path, version: int) -> Path:
    return Path(graph_dir) / "snapshots" / f"v{version:03d}.json"


def save_snapshot(graph_dir: str | Path, graph: Graph) -> Path:
    """Validate, write the next vNNN.json (never overwrites) and update latest.json."""
    graph_dir = Path(graph_dir)
    graph = Graph.model_validate(graph.model_dump())  # re-validate
    vs = _versions(graph_dir / "snapshots")
    version = (vs[-1] + 1) if vs else 1
    canon = _canonical(graph, version)
    path = snapshot_path(graph_dir, version)
    _atomic_write(path, _dump(canon))
    _atomic_write(graph_dir / "latest.json",
                  json.dumps({"version": version, "path": f"snapshots/{path.name}"}, indent=2) + "\n")
    return path


def load_snapshot(graph_dir: str | Path, version: int) -> Graph:
    p = snapshot_path(Path(graph_dir), version)
    if not p.exists():
        raise GraphStoreError(f"no such snapshot: {p}")
    return parse_graph(p.read_text())


def load_latest(graph_dir: str | Path) -> Graph | None:
    graph_dir = Path(graph_dir)
    ptr = graph_dir / "latest.json"
    if not ptr.exists():
        return None
    try:
        rel = json.loads(ptr.read_text())["path"]
    except (ValueError, KeyError, TypeError) as e:
        raise GraphStoreError(f"corrupt latest.json: {e}") from e
    target = (graph_dir / rel).resolve()
    if graph_dir.resolve() not in target.parents:
        raise GraphStoreError("latest.json points outside graph dir")
    if not target.exists():
        raise GraphStoreError(f"latest.json points to missing file: {rel}")
    return parse_graph(target.read_text())


def generate_json_schema(graph_dir: str | Path | None = None) -> dict:
    schema = Graph.model_json_schema()
    if graph_dir is not None:
        _atomic_write(Path(graph_dir) / "schema.json", json.dumps(schema, indent=2) + "\n")
    return schema


def diff_graphs(old: Graph | None, new: Graph) -> GraphDiff:
    old = old or Graph()

    def cmp(a, b):
        ao = {x.id: x.model_dump() for x in a}
        bo = {x.id: x.model_dump() for x in b}
        return (sorted(bo.keys() - ao.keys()), sorted(ao.keys() - bo.keys()),
                sorted(i for i in ao.keys() & bo.keys() if ao[i] != bo[i]))

    an, rn, cn = cmp(old.nodes, new.nodes)
    ae, re_, ce = cmp(old.edges, new.edges)
    return GraphDiff(added_nodes=an, removed_nodes=rn, changed_nodes=cn,
                     added_edges=ae, removed_edges=re_, changed_edges=ce)
