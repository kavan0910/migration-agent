"""Walking skeleton (A-10) and inventory (A-06): init -> extract -> render with no LLM."""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel

from .extract.config import extract_config, write_config
from .extract.manifests import parse_manifests, write_manifests
from .extract.treesitter import extract_ast, write_ast_facts
from .graph.models import ID_PREFIX
from .graph import Evidence, Graph, Node, Registry, save_snapshot
from .ingest import ingest
from .project import Project

ROUTE_RES = [
    re.compile(r"""\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['"]([^'"]+)"""),
    re.compile(r"""@\w+\.route\(\s*['"]([^'"]+)['"](?:.*methods=\[['"](\w+))?"""),
    re.compile(r"""@(Get|Post|Put|Patch|Delete)Mapping\b(?:\(\s*(?:value\s*=\s*)?['"]([^'"]*)['"])?"""),
    re.compile(r"""^\s*(?:re_)?(path)\(\s*r?['"]([^'"]*)['"]\s*,"""),
]


class InventoryItem(BaseModel):
    id: str
    kind: str  # source_file | route | config_key | dependency
    name: str
    file: str | None = None
    line: int | None = None


class Inventory(BaseModel):
    items: list[InventoryItem]
    counts: dict[str, int]


def _routes(code: Path, files: list[str]) -> list[InventoryItem]:
    out = []
    for rel in files:
        try:
            lines = (code / rel).read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            for rx in ROUTE_RES:
                m = rx.search(line)
                if m:
                    g = [x for x in m.groups() if x]
                    name = " ".join("ANY" if x == "path" else x.upper() if x.lower() in ("get", "post", "put", "patch", "delete") else x for x in g)
                    out.append(InventoryItem(id="", kind="route", name=name, file=rel, line=i))
                    break
    return out


def build_inventory(proj: Project, ast, manifests, config) -> Inventory:
    files = [f.path for f in ast.files]
    items = [InventoryItem(id="", kind="source_file", name=p, file=p) for p in files]
    items += _routes(proj.code, files)
    items += [InventoryItem(id="", kind="config_key", name=k.key, file=k.file, line=k.line) for k in config.keys]
    for m in manifests.manifests:
        items += [InventoryItem(id="", kind="dependency", name=d.name, file=m.path) for d in m.dependencies]
    items.sort(key=lambda i: (i.kind, i.file or "", i.line or 0, i.name))
    for n, it in enumerate(items, 1):
        it.id = f"INV-{n:05d}"
    counts: dict[str, int] = {}
    for it in items:
        counts[it.kind] = counts.get(it.kind, 0) + 1
    return Inventory(items=items, counts=counts)


def run_ingest(proj: Project, source: str):
    return ingest(source, proj.code)


def run_extract(proj: Project) -> Inventory:
    ast = extract_ast(proj.code)
    write_ast_facts(ast, proj.facts / "ast.json")
    manifests = parse_manifests(proj.code)
    write_manifests(manifests, proj.facts)
    config = extract_config(proj.code)
    write_config(config, proj.facts)
    inv = build_inventory(proj, ast, manifests, config)
    proj.inventory_path.write_text(inv.model_dump_json(indent=2))
    return inv


def build_fact_graph(proj: Project) -> Graph:
    """Facts-only graph: files -> components, routes -> endpoints, deps -> externals, config keys -> config."""
    inv = Inventory.model_validate_json(proj.inventory_path.read_text())
    reg = Registry(proj.graph_dir / "registry.json")
    spec = {"source_file": "component", "route": "endpoint", "dependency": "external_dependency", "config_key": "config"}
    nodes: dict[tuple, Node] = {}
    for it in inv.items:
        t = spec[it.kind]
        module = str(Path(it.file).parent) if it.file and t != "external_dependency" else (it.file or "")
        sym = it.name if t != "component" else it.file
        key = (t, module, f"{sym}@{it.line}" if t in ("endpoint", "config") else sym)
        ev = [Evidence(file=it.file, line_start=it.line, line_end=it.line)] if it.file else []
        if key in nodes:
            nodes[key].inventory_refs.append(it.id)
            continue
        nodes[key] = Node(id=f"{ID_PREFIX[t]}-0000", type=t, name=it.name, module=module, qualified_symbol=key[2],
                          responsibility="(facts only: not yet analysed)", evidence=ev, inventory_refs=[it.id])
    ids = reg.allocate_nodes(list(nodes))
    for k, n in nodes.items():
        n.id = ids[k]
    reg.save()
    return Graph(nodes=sorted(nodes.values(), key=lambda n: n.id))


def render_doc(proj: Project, graph: Graph, inv: Inventory) -> Path:
    meta = proj.meta()
    L = [f"# Understanding: {meta.name}", "", f"_Facts-only skeleton (no LLM). Source: `{meta.source}`. Graph v{graph.version}._", "",
         "## Coverage inventory", ""]
    L += [f"- {k}: {v}" for k, v in sorted(inv.counts.items())]
    mapped = {r for n in graph.nodes for r in n.inventory_refs}
    L += ["", f"Mapped {len(mapped)} of {len(inv.items)} inventory items.", ""]
    titles = {"component": "Components", "endpoint": "Endpoints", "external_dependency": "External dependencies", "config": "Configuration"}
    for t, title in titles.items():
        ns = [n for n in graph.nodes if n.type == t]
        if not ns:
            continue
        L += [f"## {title}", ""]
        for n in ns:
            ev = n.evidence[0] if n.evidence else None
            loc = f" `{ev.file}:{ev.line_start}`" if ev and ev.line_start else (f" `{ev.file}`" if ev else "")
            L.append(f"- <a id=\"{n.id}\"></a>**{n.id}** {n.name}{loc} · confidence {n.confidence}")
        L.append("")
    out = proj.docs / "understanding.latest.md"
    out.write_text("\n".join(L))
    return out


def run_render(proj: Project) -> Path:
    inv = Inventory.model_validate_json(proj.inventory_path.read_text())
    g = build_fact_graph(proj)
    save_snapshot(proj.graph_dir, g)
    from .graph import load_latest
    return render_doc(proj, load_latest(proj.graph_dir), inv)
