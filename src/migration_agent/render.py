"""Understanding Document renderer (B-09): pure, deterministic graph -> markdown (Jinja2, no LLM)."""
from __future__ import annotations

import re
from collections import defaultdict

from jinja2 import Environment, PackageLoader, StrictUndefined

from .graph.models import Edge, Graph, Node
from .graph.store import diff_graphs

SECTIONS = [
    "Purpose & Capabilities", "Boundary & External Dependencies", "Component Responsibilities",
    "Endpoints & API Surface", "Data Model & Storage", "Business Rules", "Integrations & Contracts",
    "Background Jobs & Consumers", "Configuration & Environment", "Auth, Error Handling & Cross-Cutting",
    "Existing Tests", "Quirks & Suspicious Code", "Blind Spots / low confidence",
    "Open Questions & Assumptions", "Coverage Matrix",
]
_XCUT = re.compile(r"\b(auth\w*|login|session|token|permission\w*|error\w*|exception\w*|logg\w*|middleware|validat\w*|retry|audit)\b", re.I)
_TEST = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]*|_test\.\w+$|\.(test|spec)\.\w+$", re.I)

_env = Environment(loader=PackageLoader("migration_agent", "templates"), undefined=StrictUndefined,
                   keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=False, autoescape=False)


def _by_id(xs):
    return sorted(xs, key=lambda x: x.id)


def _fmt_ev(e) -> str:
    s = e.file + (f":{e.line_start}" if e.line_start else "")
    if e.line_end and e.line_end != e.line_start:
        s += f"-{e.line_end}"
    return s


def _is_test(n: Node) -> bool:
    return any(_TEST.search(e.file) for e in n.evidence)


def _texts(v) -> list[str]:
    out = []
    for x in v or []:
        if isinstance(x, dict):
            t = x.get("text") or x.get("question") or x.get("assumption") or str(sorted(x.items()))
            if x.get("id"):
                t = f"{x['id']}: {t}"
            out.append(str(t))
        else:
            out.append(str(x))
    return out


def _clean(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]+\n", "\n", text).rstrip("\n") + "\n"


def _sections(g: Graph, meta: dict) -> dict:
    nodes = {n.id: n for n in g.nodes}
    of = lambda t: _by_id(n for n in g.nodes if n.type == t)  # noqa: E731
    links: dict[str, list[str]] = defaultdict(list)
    for e in _by_id(g.edges):
        links[e.source].append(f"{e.type} [{e.target}](#{e.target})")
        links[e.target].append(f"{e.type} by [{e.source}](#{e.source})")
    ref = lambda e, *types: e.type in types  # noqa: E731

    def edges_for(types, *node_types):
        return [e for e in _by_id(g.edges) if e.type in types or nodes[e.source].type in node_types or nodes[e.target].type in node_types]

    comps: dict[str, list[Node]] = defaultdict(list)
    for n in of("component"):
        comps[n.module or "(root)"].append(n)
    xcut = [n for n in _by_id(g.nodes) if n.type in ("component", "business_rule", "config")
            and _XCUT.search(f"{n.name} {n.module} {n.responsibility}")]
    return {
        "links": links,
        "deps": of("external_dependency"),
        "dep_edges": [e for e in _by_id(g.edges) if e.type == "depends_on"],
        "components": sorted(comps.items()),
        "dead": of("dead_code"),
        "endpoints": of("endpoint"),
        "entities": of("data_entity"),
        "data_edges": [e for e in _by_id(g.edges) if e.type in ("reads", "writes")],
        "rules": of("business_rule"),
        "integrations": of("integration"),
        "int_edges": [e for e in _by_id(g.edges) if e.type in ("calls", "publishes", "consumes")
                      and "integration" in (nodes[e.source].type, nodes[e.target].type)],
        "jobs": of("job"),
        "configs": of("config"),
        "xcut": xcut,
        "tests": [n for n in _by_id(g.nodes) if _is_test(n)],
        "bugs": [n for n in _by_id(g.nodes) if n.suspected_bug],
        "quirks": [n for n in _by_id(g.nodes) if n.quirk],
        "blind_nodes": [n for n in _by_id(g.nodes) if n.confidence in ("none", "low")],
        "blind_edges": [e for e in _by_id(g.edges) if e.confidence in ("none", "low")],
        "fallback": sorted(meta.get("fallback_parsed_files") or []),
        "questions": _texts(meta.get("open_questions")),
        "assumptions": _texts(meta.get("assumptions")),
    }


def _node_fields(a: Node, b: Node) -> list[str]:
    da, db = a.model_dump(), b.model_dump()
    fields = sorted(k for k in db if da[k] != db[k])
    if a.confidence != b.confidence:
        fields.append(f"confidence {a.confidence} -> {b.confidence}")
    return fields


def render_changelog(old: Graph | None, new: Graph, reasons: dict[str, str] | None = None) -> str:
    """Changelog (added/removed/changed by stable ID) computed on the graphs, not on prose."""
    reasons = reasons or {}
    d = diff_graphs(old, new)
    o, nn = old or Graph(), new
    on = {n.id: n for n in o.nodes}
    nw = {n.id: n for n in nn.nodes}
    oe = {e.id: e for e in o.edges}
    ne = {e.id: e for e in nn.edges}

    def why(i):
        return f" (reason: {reasons[i]})" if i in reasons else ""

    def nrow(i, src, verb):
        n = src[i]
        return f"[{i}](#{i}) {n.name} ({n.type}){why(i)}" if verb != "removed" else f"{i} {n.name} ({n.type}){why(i)}"

    def erow(i, src):
        e = src[i]
        return f"{i} {e.source} -{e.type}-> {e.target}{why(i)}"

    changed = []
    for i in d.changed_nodes:
        fields = ", ".join(_node_fields(on[i], nw[i]))
        changed.append(f"[{i}](#{i}) {nw[i].name}: changed {fields}{why(i)}")
    changed_e = []
    for i in d.changed_edges:
        fields = ", ".join(sorted(k for k, v in ne[i].model_dump().items() if oe[i].model_dump()[k] != v))
        changed_e.append(f"{i} {ne[i].source} -{ne[i].type}-> {ne[i].target}: changed {fields}{why(i)}")
    groups = [
        ("Added", [nrow(i, nw, "added") for i in d.added_nodes] + [erow(i, ne) for i in d.added_edges]),
        ("Removed", [nrow(i, on, "removed") for i in d.removed_nodes] + [erow(i, oe) for i in d.removed_edges]),
        ("Changed", changed + changed_e),
    ]
    return _clean(_env.get_template("changelog.md.j2").render(
        empty=d.empty, old_version=o.version, new_version=nn.version, groups=groups))


def render_understanding(graph: Graph, meta: dict, inventory_counts: dict, coverage: dict | None = None,
                         previous: Graph | None = None) -> str:
    """Render the 15-section Understanding Document from the graph alone.

    meta keys (all optional): name, source, description/purpose, fallback_parsed_files,
    open_questions, assumptions, change_reasons ({id: reason}).
    coverage: any flat dict (e.g. mapped/unmapped/excluded counts); lists are rendered as counts.
    """
    meta = meta or {}
    counts: dict[str, int] = defaultdict(int)
    for n in graph.nodes:
        counts[n.type] += 1
    cov = []
    for k, v in sorted((coverage or {}).items()):
        cov.append((k, ", ".join(map(str, v)) if isinstance(v, (list, tuple, set)) and v and len(v) <= 20
                    else len(v) if isinstance(v, (list, tuple, set)) else v))
    sec = _sections(graph, meta)
    changelog = render_changelog(previous, graph, meta.get("change_reasons")) if previous is not None else ""
    out = _env.get_template("understanding.md.j2").render(
        graph=graph, name=meta.get("name", "(unnamed)"), source=meta.get("source", ""),
        description=meta.get("description", ""), purpose=meta.get("purpose") or meta.get("description", ""),
        toc=[(f"[{i}]", t) for i, t in enumerate(SECTIONS, 1)], counts=sorted(counts.items()),
        sec=sec, links=sec["links"], inventory_counts=dict(inventory_counts or {}), cov=cov, changelog=changelog)
    return _clean(out)
