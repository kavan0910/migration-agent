"""C-08 changelog: docs/changelog.md from consecutive graph snapshots, annotated with WHY and evidence."""
from __future__ import annotations

import re
from pathlib import Path

from ..graph import diff_graphs, load_snapshot
from ..graph.models import Edge, Evidence, Graph, Node
from ..project import Project
from ._io import atomic_write
from .reanalyze import load_log


def _versions(project: Project) -> list[int]:
    d = project.graph_dir / "snapshots"
    if not d.is_dir():
        return []
    return sorted(int(m.group(1)) for p in d.iterdir() if (m := re.fullmatch(r"v(\d+)\.json", p.name)))


def _ev(evs: list[Evidence]) -> str:
    out = []
    for e in sorted(evs, key=lambda e: (e.file, e.line_start or 0, e.line_end or 0)):
        loc = e.file
        if e.line_start:
            loc += f":{e.line_start}" + (f"-{e.line_end}" if e.line_end and e.line_end != e.line_start else "")
        out.append(loc)
    return ", ".join(dict.fromkeys(out))


def _label(x: Node | Edge) -> str:
    if isinstance(x, Node):
        return f"{x.id} {x.name} ({x.type})"
    return f"{x.id} {x.source} -{x.type}-> {x.target}"


def _changed_fields(a: Node | Edge, b: Node | Edge) -> str:
    da, db = a.model_dump(), b.model_dump()
    fields = sorted(k for k in db if da[k] != db[k])
    if a.confidence != b.confidence:
        fields.append(f"confidence {a.confidence}->{b.confidence}")
    return ", ".join(fields)


def _reasons(run: dict | None, item_id: str, node_ids: set[str]) -> str:
    """Triggers (resolution/comment ids) responsible for a change to item_id."""
    if not run:
        return ""
    direct = [t for t in run["triggers"] if item_id in t.get("nodes", [])]
    triggers = direct or run["triggers"]   # new/removed items: attribute to the whole run
    return ", ".join(f"{t['kind']} {t['id']}" for t in triggers)


def _section(v_old: Graph | None, v_new: Graph, run: dict | None) -> list[str]:
    d = diff_graphs(v_old, v_new)
    on = {n.id: n for n in (v_old.nodes if v_old else [])} | {e.id: e for e in (v_old.edges if v_old else [])}
    nw = {n.id: n for n in v_new.nodes} | {e.id: e for e in v_new.edges}
    head = f"## v{v_new.version}" + (f" (from v{v_old.version})" if v_old else " (initial analysis)")
    lines = [head, ""]
    if v_old is None:
        lines += [f"- {len(v_new.nodes)} nodes, {len(v_new.edges)} edges", ""]
        return lines
    if run:
        lines += ["Triggered by: " + ", ".join(f"{t['kind']} {t['id']}" for t in run["triggers"]), ""]
    if d.empty:
        return lines + ["No changes.", ""]
    nodes = set(nw)

    def row(i: str, verb: str, src: dict) -> str:
        x = src[i]
        s = f"- {verb} {_label(x)}"
        if verb == "changed":
            s += f": {_changed_fields(on[i], nw[i])}"
        ev = _ev(x.evidence)
        if ev:
            s += f" [evidence: {ev}]"
        why = _reasons(run, i, nodes)
        if why:
            s += f" (why: {why})"
        return s

    groups = [("Added", "added", d.added_nodes + d.added_edges, nw),
              ("Removed", "removed", d.removed_nodes + d.removed_edges, on),
              ("Changed", "changed", d.changed_nodes + d.changed_edges, nw)]
    for title, verb, ids, src in groups:
        if ids:
            lines += [f"### {title}", *[row(i, verb, src) for i in ids], ""]
    return lines


def generate_changelog(project: Project) -> Path:
    versions = _versions(project)
    runs = {r["to_version"]: r for r in load_log(project)["runs"]}
    out = ["# Changelog", ""]
    if not versions:
        out += ["No graph snapshots yet.", ""]
    graphs = {v: load_snapshot(project.graph_dir, v) for v in versions}
    # newest first
    for idx in range(len(versions) - 1, -1, -1):
        v = versions[idx]
        prev = graphs[versions[idx - 1]] if idx else None
        out += _section(prev, graphs[v], runs.get(v))
    path = project.docs / "changelog.md"
    atomic_write(path, "\n".join(out).rstrip() + "\n")
    return path
