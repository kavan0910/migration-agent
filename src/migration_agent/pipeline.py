"""Walking skeleton (A-10) and inventory (A-06): init -> extract -> render with no LLM."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .extract.django_mounts import apply_django_prefix, django_mount_prefixes
from .extract.express_mounts import apply_prefix, mount_prefixes
from .extract.frameworks import extract_routes
from .extract.schemas import extract_schemas
from .extract.specs import extract_specs, route_identity
from .extract.config import extract_config, write_config
from .extract.manifests import parse_manifests, write_manifests
from .extract.treesitter import SKIP_DIRS, extract_ast, write_ast_facts
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
    kind: str  # source_file | route | config_key | dependency | schema_entity
    name: str
    file: str | None = None
    line: int | None = None
    attrs: dict[str, Any] | None = None  # extractor detail, e.g. schema_entity columns


class Inventory(BaseModel):
    items: list[InventoryItem]
    counts: dict[str, int]
    warnings: list[str] = []


MAX_SCAN_BYTES = 2 * 1024 * 1024
_JS_EXT = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
_SCHEMA_EXT = (".sql", ".prisma", ".py", ".ts", ".js")
_SPEC_EXT = (".json", ".yaml", ".yml", ".proto")


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        return path.read_text(errors="ignore")
    except OSError:
        return None


def _legacy_routes(rel: str, lines: list[str], skip: set[int]) -> list[InventoryItem]:
    low = rel.lower()
    rxs = ROUTE_RES
    if low.endswith(".py"):
        rxs = [ROUTE_RES[3]]  # Flask/FastAPI handled by extract.frameworks; keep Django path()
    elif low.endswith(".java"):
        rxs = []
    out = []
    for i, line in enumerate(lines, 1):
        if i in skip:
            continue
        for rx in rxs:
            m = rx.search(line)
            if m:
                g = [x for x in m.groups() if x]
                name = " ".join("ANY" if x == "path" else x.upper() if x.lower() in ("get", "post", "put", "patch", "delete") else x for x in g)
                out.append(InventoryItem(id="", kind="route", name=name, file=rel, line=i))
                break
    return out


def _routes(code: Path, files: list[str]) -> list[InventoryItem]:
    """Route inventory: framework extractors (Spring/Flask/FastAPI/Rails/ASP.NET) + legacy line regexes (Express/Django)."""
    out = []
    for rel in files:
        text = _read(code / rel)
        if text is None:
            continue
        found = extract_routes(rel, text)
        out += [InventoryItem(id="", kind="route", name=f.name, file=f.file, line=f.line) for f in found]
        out += _legacy_routes(rel, text.splitlines(), {f.line for f in found})
    return _apply_django_mounts(code, files, _apply_express_mounts(code, files, out))


def _apply_express_mounts(code: Path, files: list[str], items: list[InventoryItem]) -> list[InventoryItem]:
    """Rewrite router-relative Express route names to full paths using app.use/router.use mount chains."""
    js = [f for f in files if f.endswith((".js", ".mjs", ".cjs", ".ts"))]
    texts = {f: t for f in js if (t := _read(code / f)) is not None and ".use(" in t}
    if not texts:
        return items
    for f in js:                                         # route files may not mention .use( themselves
        if f not in texts and (t := _read(code / f)) is not None:
            texts[f] = t
    prefixes = mount_prefixes(texts)
    if not prefixes:
        return items
    return [it.model_copy(update={"name": apply_prefix(it.name, prefixes[it.file])})
            if it.kind == "route" and it.file in prefixes else it for it in items]


def _apply_django_mounts(code: Path, files: list[str], items: list[InventoryItem]) -> list[InventoryItem]:
    """Prefix Django route names with the path of the include() chain that reaches their urls file."""
    texts = {}
    for f in files:
        if f.endswith(".py") and (t := _read(code / f)) is not None and "urlpatterns" in t:
            texts[f] = t
    prefixes = django_mount_prefixes(texts) if len(texts) > 1 else {}
    if not prefixes:
        return items
    return [it.model_copy(update={"name": apply_django_prefix(it.name, prefixes[it.file])})
            if it.kind == "route" and it.file in prefixes else it for it in items]


def _walk(code: Path) -> list[str]:
    out = []
    for dp, dns, fns in os.walk(code):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        for fn in sorted(fns):
            out.append(Path(dp, fn).relative_to(code).as_posix())
    return out


def _schemas_and_specs(code: Path, files: list[str]) -> tuple[list[InventoryItem], list[str]]:
    """schema_entity items (Flyway/Alembic/Prisma/TypeORM/proto messages) and spec routes (OpenAPI, proto rpc)."""
    items: list[InventoryItem] = []
    warns: list[str] = []
    for rel in files:
        low = rel.lower()
        want_schema = low.endswith(_SCHEMA_EXT)
        want_spec = low.endswith(_SPEC_EXT)
        if not (want_schema or want_spec):
            continue
        try:
            big = (code / rel).stat().st_size > MAX_SCAN_BYTES
        except OSError:
            continue
        if big:
            if want_spec and (not low.endswith(".json") or "openapi" in low or "swagger" in low):
                warns.append(f"{rel}: larger than {MAX_SCAN_BYTES} bytes, skipped")
            continue
        text = _read(code / rel)
        if text is None:
            continue
        found = extract_schemas(rel, text) if want_schema else []
        if want_spec:
            f2, w = extract_specs(rel, text)
            found += f2
            warns += w
        for f in found:
            items.append(InventoryItem(id="", kind=f.kind, name=f.name, file=f.file, line=f.line, attrs=f.attrs or None))
    return items, warns


def build_inventory(proj: Project, ast, manifests, config) -> Inventory:
    files = [f.path for f in ast.files]
    items = [InventoryItem(id="", kind="source_file", name=p, file=p) for p in files]
    items += _routes(proj.code, files)
    extra, warnings = _schemas_and_specs(proj.code, _walk(proj.code))
    items += extra
    items += [InventoryItem(id="", kind="config_key", name=k.key, file=k.file, line=k.line) for k in config.keys]
    for m in manifests.manifests:
        items += [InventoryItem(id="", kind="dependency", name=d.name, file=m.path) for d in m.dependencies]
    items.sort(key=lambda i: (i.kind, i.file or "", i.line or 0, i.name))
    for n, it in enumerate(items, 1):
        it.id = f"INV-{n:05d}"
    counts: dict[str, int] = {}
    for it in items:
        counts[it.kind] = counts.get(it.kind, 0) + 1
    return Inventory(items=items, counts=counts, warnings=sorted(set(warnings)))


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
    spec = {"source_file": "component", "route": "endpoint", "dependency": "external_dependency", "config_key": "config",
            "schema_entity": "data_entity"}
    nodes: dict[tuple, Node] = {}
    from collections import Counter
    dup = Counter((it.kind, it.file, it.name) for it in inv.items)
    for it in inv.items:
        t = spec[it.kind]
        module = it.file or ""
        sym = it.name if t != "component" else it.file
        if t == "endpoint":
            sym = route_identity(sym)  # operationId suffix is presentation, not identity
        key = (t, module, f"{sym}@{it.line}" if t in ("endpoint", "config", "data_entity") and dup[(it.kind, it.file, it.name)] > 1 else sym)
        ev = [Evidence(file=it.file, line_start=it.line, line_end=it.line)] if it.file else []
        if key in nodes:
            nodes[key].inventory_refs.append(it.id)
            continue
        nodes[key] = Node(id=f"{ID_PREFIX[t]}-0000", type=t, name=it.name, module=module, qualified_symbol=key[2],
                          responsibility="(facts only: not yet analysed)", evidence=ev, inventory_refs=[it.id])
    ids = reg.allocate_nodes(list(nodes))
    for k, n in nodes.items():
        n.id = ids[k]
    from .extract.deps import extract_deps
    from .graph.models import Edge
    comp_id = {n.module: n.id for n in nodes.values() if n.type == "component"}
    deps = [(s, d, ln) for s, d, ln in extract_deps(proj.code, list(comp_id)) if s in comp_id and d in comp_id]
    eids = reg.allocate_edges([(comp_id[s], "calls", comp_id[d]) for s, d, _ in deps])
    edges = [Edge(id=eids[(comp_id[s], "calls", comp_id[d])], source=comp_id[s], target=comp_id[d], type="calls",
                  evidence=[Evidence(file=s, line_start=ln, line_end=ln)]) for s, d, ln in deps]
    reg.save()
    return Graph(nodes=sorted(nodes.values(), key=lambda n: n.id), edges=sorted(edges, key=lambda e: e.id))


def render_doc(proj: Project, graph: Graph, inv: Inventory) -> Path:
    meta = proj.meta()
    L = [f"# Understanding: {meta.name}", "", f"_Facts-only skeleton (no LLM). Source: `{meta.source}`. Graph v{graph.version}._", "",
         "## Coverage inventory", ""]
    L += [f"- {k}: {v}" for k, v in sorted(inv.counts.items())]
    mapped = {r for n in graph.nodes for r in n.inventory_refs}
    L += ["", f"Mapped {len(mapped)} of {len(inv.items)} inventory items.", ""]
    titles = {"component": "Components", "endpoint": "Endpoints", "data_entity": "Data entities", "external_dependency": "External dependencies", "config": "Configuration"}
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
