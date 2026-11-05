"""B-10 analyze pipeline: facts -> chunks -> LLM patches -> merge -> cross-ref -> verify -> coverage -> render."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..audit import AuditLog
from ..extract.treesitter import AstFacts
from ..graph import Registry, save_snapshot
from ..graph.models import Graph
from ..llm.base import BudgetExceeded, LLMClient
from ..llm.litellm_client import LLMConfig, estimate_cost
from ..pipeline import Inventory, build_fact_graph
from ..project import Project
from ..render import render_understanding
from ..sandbox.tools import ReadOnlyTools
from ..viz.graph_html import write_graph_html
from .chunker import plan_chunks, render_chunk_text
from .coverage import check_coverage
from .crossref import resolve_cross_refs
from .evidence import verify_evidence
from .gapfill import fill_gaps
from .merger import Checkpoint, merge_patches
from .module_analyzer import analyze_chunk
from .patch import ChunkPatch
from .rules import extract_rules


def _summary(graph: Graph, limit: int = 60) -> str:
    return "\n".join(f"{n.id} {n.type} {n.qualified_symbol}" for n in graph.nodes[:limit]) or "(empty)"


def estimate(proj: Project, config: LLMConfig | None = None) -> tuple[int, float]:
    ast = AstFacts.model_validate_json((proj.facts / "ast.json").read_text())
    chunks = plan_chunks(proj.code, ast)
    tasks = []
    for c in chunks:
        tasks += [("chunk_extract", c.est_tokens + 1500, 2000), ("business_rules", c.est_tokens + 1500, 1500)]
    return len(chunks), estimate_cost(tasks, config)


def run_analyze(proj: Project, llm: LLMClient, *, log: Callable[[str], None] = lambda s: None) -> dict:
    audit = AuditLog(proj.audit_dir / "audit.jsonl")
    ast = AstFacts.model_validate_json((proj.facts / "ast.json").read_text())
    inv = Inventory.model_validate_json(proj.inventory_path.read_text())
    reg = Registry(proj.graph_dir / "registry.json")
    base = build_fact_graph(proj)
    tools = ReadOnlyTools(proj.code)
    ckpt = Checkpoint(proj.root / "checkpoint")
    chunks = plan_chunks(proj.code, ast)
    done = ckpt.completed_chunk_ids()
    stopped = None
    with audit.span("analyst", "analyze_chunks", chunks=len(chunks), resumed=len(done)):
        for c in chunks:
            if c.id in done:
                log(f"{c.id}: resumed from checkpoint")
                continue
            text = render_chunk_text(proj.code, c)
            facts = ", ".join(c.files)
            try:
                p1 = analyze_chunk(llm, c.id, text, facts, _summary(base))
                p2 = extract_rules(llm, c.id, text, _summary(base))
            except BudgetExceeded as e:
                stopped = str(e)
                log(f"budget reached at {c.id}: {e}")
                break
            ckpt.save_patch(ChunkPatch(chunk_id=c.id, nodes=p1.nodes + p2.nodes, edges=p1.edges + p2.edges,
                                       unresolved=p1.unresolved + p2.unresolved))
            log(f"{c.id}: {len(p1.nodes)} nodes, {len(p2.nodes)} rules")
    res = merge_patches(ckpt.load_patches(), reg, base=base)
    graph = res.graph
    with audit.span("analyst", "cross_ref"):
        if res.unresolved and not stopped:
            xp = resolve_cross_refs(llm, graph, res.unresolved, tools)
            res = merge_patches(ckpt.load_patches() + [xp], reg, base=base)
            graph = res.graph
    ver = verify_evidence(graph, proj.code)
    graph = ver.graph
    cov = check_coverage(inv, graph)
    if not cov.complete and not stopped:
        with audit.span("analyst", "gap_fill"):
            gf = fill_gaps(llm, cov, graph, tools)
        res = merge_patches(ckpt.load_patches() + [gf.patch], reg, base=base)
        graph = verify_evidence(res.graph, proj.code).graph
        cov = check_coverage(inv, graph, {k: v["reason"] for k, v in gf.classified.items() if v.get("status") == "excluded"})
    reg.save()
    save_snapshot(proj.graph_dir, graph)
    from ..graph import load_latest
    graph = load_latest(proj.graph_dir)
    prev = None
    meta = {"name": proj.meta().name, "source": proj.meta().source,
            "fallback_parsed_files": [f.path for f in ast.files if f.parse_status == "fallback"]}
    doc = render_understanding(graph, meta, inv.counts, coverage=cov.model_dump(exclude={"unmapped_items"}) | {"unmapped_ids": [i.id for i in cov.unmapped_items]}, previous=prev)
    (proj.docs / "understanding.latest.md").write_text(doc)
    write_graph_html(proj.docs / "graph.html", graph, title=meta["name"])
    return {"chunks": len(chunks), "nodes": len(graph.nodes), "edges": len(graph.edges),
            "coverage_complete": cov.complete, "unmapped": cov.unmapped, "rejected": len(ver.rejected),
            "stopped": stopped}
