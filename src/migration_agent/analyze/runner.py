"""B-10 analyze pipeline: facts -> chunks -> LLM patches -> merge -> cross-ref -> verify -> coverage -> render."""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from ..audit import AuditLog
from ..extract.treesitter import AstFacts
from ..graph import Registry, save_snapshot
from ..graph.models import Graph
from ..llm.base import BudgetExceeded, LLMAuthError, LLMClient, LLMResponse
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
from .merger import Checkpoint, merge_patches, dedupe_fact_endpoints, collapse_entity_components
from .incremental import (Manifest, Plan, build_manifest, compute_deps, hash_files, load_cost_total,
                          plan_reuse, record_session_cost)
from .gapfill import GapFillResult
from .module_analyzer import PROMPT_VERSION, AnalysisError, analyze_chunk
from .patch import ChunkPatch
from .rules import RULES_PROMPT_VERSION, extract_rules


def _summary(graph: Graph, limit: int = 60) -> str:
    return "\n".join(f"{n.id} {n.type} {n.qualified_symbol}" for n in graph.nodes[:limit]) or "(empty)"


NEIGHBOUR_MAX_DEPS = 6
NEIGHBOUR_MAX_LINES = 40
NEIGHBOUR_MAX_CHARS = 4000


def _prompt_version() -> str:
    return f"{PROMPT_VERSION}|{RULES_PROMPT_VERSION}"


def _model_id(llm) -> str:
    models = getattr(getattr(llm, "config", None), "models", None)
    return json.dumps(models, sort_keys=True) if isinstance(models, dict) else ""


def _neighbour_summary(chunk, patches: dict[str, ChunkPatch]) -> str:
    """Bounded summary of already-analysed neighbour chunks (the ones this chunk imports) so deep dependency
    trees are split into budgeted chunks instead of growing the context without limit."""
    lines: list[str] = []
    for dep in sorted(chunk.depends_on_chunks, reverse=True)[:NEIGHBOUR_MAX_DEPS]:
        p = patches.get(dep)
        if p is None:
            continue
        for n in p.nodes[:12]:
            lines.append(f"- {n.type} {n.qualified_symbol} [{dep}]")
    if not lines:
        return ""
    out = "\n".join(lines[:NEIGHBOUR_MAX_LINES])[:NEIGHBOUR_MAX_CHARS]
    return "Already analysed neighbouring chunks (context only; do not re-emit):\n" + out + "\n\n"


def _read_ast(proj: Project) -> AstFacts:
    return AstFacts.model_validate_json((proj.facts / "ast.json").read_text())


def build_plan(proj: Project, ast: AstFacts, *, incremental: bool = False, model: str = "",
               chunks: list | None = None, context_tokens: int | None = None) -> tuple[Plan, Checkpoint]:
    """Read-only: plan chunks, hash files and decide which saved patches can be reused."""
    ckpt = Checkpoint(proj.root / "checkpoint")
    if chunks is None:
        chunks = plan_chunks(proj.code, ast, **({"context_tokens": context_tokens} if context_tokens else {}))
    paths = sorted({f for c in chunks for f in c.files})
    hashes = hash_files(proj.code, paths)
    deps = compute_deps(proj.code, paths)
    old = Manifest.load(ckpt.dir / "manifest.json")
    plan = plan_reuse(chunks, hashes, deps, old, ckpt.completed_chunk_ids(), set(ckpt.load_failures()),
                      prompt_version=_prompt_version(), incremental=incremental)
    return plan, ckpt


def estimate(proj: Project, config: LLMConfig | None = None, *, incremental: bool = False,
             context_tokens: int | None = None) -> tuple[int, float]:
    """(total chunks, estimated USD for the chunks that still need analysis)."""
    ast = _read_ast(proj)
    plan, _ = build_plan(proj, ast, incremental=incremental, context_tokens=context_tokens)
    tasks = []
    for c in plan.pending_chunks:
        tasks += [("chunk_extract", c.est_tokens + 1500, 2000), ("business_rules", c.est_tokens + 1500, 1500)]
    return len(plan.chunks), estimate_cost(tasks, config)


class _Metered:
    """Wraps an LLMClient: accumulates cost/tokens/calls (thread-safe) and enforces an optional run budget."""

    def __init__(self, inner, max_usd: float | None = None):
        self.inner, self.max_usd = inner, max_usd
        self.usd, self.tokens_in, self.tokens_out, self.calls = 0.0, 0, 0, 0
        self._lock = threading.Lock()

    def complete(self, **kw) -> LLMResponse:
        with self._lock:
            if self.max_usd is not None and self.usd >= self.max_usd:
                raise BudgetExceeded(f"USD budget: spent {self.usd:.4f} >= {self.max_usd:.4f}")
        r = self.inner.complete(**kw)
        with self._lock:
            self.usd += getattr(r, "cost_usd", 0.0) or 0.0
            self.tokens_in += getattr(r, "tokens_in", 0) or 0
            self.tokens_out += getattr(r, "tokens_out", 0) or 0
            self.calls += 1
        return r

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _sig(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode())
        h.update(b"\x00")
    return h.hexdigest()


def _is_stop(e: BaseException) -> bool:
    return isinstance(e, (BudgetExceeded, LLMAuthError))


def run_analyze(proj: Project, llm: LLMClient, *, log: Callable[[str], None] = lambda s: None,
                incremental: bool = False, workers: int = 1, max_usd: float | None = None,
                context_tokens: int | None = None) -> dict:
    audit = AuditLog(proj.audit_dir / "audit.jsonl")
    if incremental:  # the source may have been re-ingested: refresh facts/inventory (no LLM involved)
        from ..pipeline import run_extract
        run_extract(proj)
    ast = _read_ast(proj)
    inv = Inventory.model_validate_json(proj.inventory_path.read_text())
    base = build_fact_graph(proj)  # allocates + saves fact-node IDs in the registry...
    reg = Registry(proj.graph_dir / "registry.json")  # ...so load it afterwards: IDs the LLM saw == final IDs
    tools = ReadOnlyTools(proj.code)
    meter = _Metered(llm, max_usd)
    llm = meter  # type: ignore[assignment]
    plan, ckpt = build_plan(proj, ast, incremental=incremental, model=_model_id(llm.inner),
                            context_tokens=context_tokens)
    chunks = plan.chunks
    ids = [c.id for c in chunks]
    log(f"plan: {len(chunks)} chunk(s); {len(plan.reuse)} reused, {len(plan.pending)} to analyse"
        + (f"; {len(plan.changed_files)} changed file(s), {len(plan.dependent_files)} dependent file(s)"
           if incremental else ""))

    # -- apply the plan to the checkpoint: reused patches are re-keyed to their new chunk ids, the rest dropped
    patches: dict[str, ChunkPatch] = {}
    old_patches = {old: ckpt.load_patch(old) for old in set(plan.reuse.values())}
    for new, old in plan.reuse.items():
        p = old_patches[old]
        if p is None:
            plan.reuse.pop(new)
            continue
        patches[new] = p.model_copy(update={"chunk_id": new})
    for cid in sorted(ckpt.completed_chunk_ids()):
        if cid not in patches:
            ckpt.delete_patch(cid)
    for cid, p in patches.items():
        if ckpt.load_patch(cid) != p:
            ckpt.save_patch(p)
    plan.pending = [c for c in ids if c not in patches]
    ckpt.save_failures({})
    build_manifest(plan, _prompt_version(), _model_id(llm.inner)).save(ckpt.dir / "manifest.json")

    failures: dict[str, dict] = {}
    flock = threading.Lock()
    stopped: str | None = None
    by_id = {c.id: c for c in chunks}
    nb_base = _summary(base)

    def record_failure(key: str, task: str, e: BaseException) -> None:
        with flock:
            failures[key] = {"task": task, "error_class": type(e).__name__, "error": str(e)[:500]}
            ckpt.save_failures(failures)
        log(f"{key}: {task} FAILED ({type(e).__name__}: {str(e)[:160]})")

    def do_chunk(c) -> tuple[ChunkPatch, bool]:
        text = render_chunk_text(proj.code, c)
        facts = ", ".join(c.files)
        summ = _neighbour_summary(c, patches) + nb_base
        ok = True
        try:
            p1 = analyze_chunk(llm, c.id, text, facts, summ)
        except Exception as e:  # noqa: BLE001
            if _is_stop(e):
                raise
            record_failure(c.id, "chunk_extract", e)
            p1, ok = ChunkPatch(chunk_id=c.id), False
        try:
            p2 = extract_rules(llm, c.id, text, summ)
        except Exception as e:  # noqa: BLE001
            if _is_stop(e):
                raise
            record_failure(c.id, "business_rules", e)
            p2, ok = ChunkPatch(chunk_id=c.id), False
        p = ChunkPatch(chunk_id=c.id, nodes=p1.nodes + p2.nodes, edges=p1.edges + p2.edges,
                       unresolved=p1.unresolved + p2.unresolved)
        return p, ok

    def finish(c, p: ChunkPatch, ok: bool) -> None:
        patches[c.id] = p
        ckpt.save_patch(p)  # partial results are preserved even for failed chunks (retried on next run)
        if ok:
            log(f"{c.id}: {len(p.nodes)} nodes")

    for cid in ids:
        if cid in plan.reuse:
            log(f"{cid}: resumed from checkpoint")

    with audit.span("analyst", "analyze_chunks", chunks=len(chunks), resumed=len(plan.reuse)):
        todo = [by_id[i] for i in plan.pending]
        try:
            if workers <= 1:
                for c in todo:
                    p, ok = do_chunk(c)
                    finish(c, p, ok)
            else:
                waiting = {c.id: {d for d in c.depends_on_chunks if d in set(plan.pending)} for c in todo}
                running: dict = {}
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    while (waiting or running) and stopped is None:
                        for cid in [k for k, v in waiting.items() if not v]:
                            del waiting[cid]
                            running[ex.submit(do_chunk, by_id[cid])] = cid
                        if not running:
                            break
                        done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                        for fut in sorted(done, key=lambda f: by_id[running[f]].order):
                            cid = running.pop(fut)
                            try:
                                p, ok = fut.result()
                            except Exception as e:  # noqa: BLE001
                                if _is_stop(e):
                                    stopped = stopped or str(e)
                                    continue
                                raise
                            finish(by_id[cid], p, ok)
                            for v in waiting.values():
                                v.discard(cid)
                    for fut in list(running):  # let in-flight calls finish and keep their results
                        try:
                            p, ok = fut.result()
                            finish(by_id[running[fut]], p, ok)
                        except Exception as e:  # noqa: BLE001
                            if _is_stop(e):
                                stopped = stopped or str(e)
        except Exception as e:  # noqa: BLE001
            if not _is_stop(e):
                raise
            stopped = str(e)
        if stopped:
            log(f"stopped: {stopped}")

    def cached_or_run(name: str, sig: str, fn):
        """Whole-graph LLM steps are cached in the checkpoint keyed by their inputs (0 calls on unchanged input)."""
        hit = ckpt.load_extra(name, sig)
        if hit is not None:
            return hit
        try:
            out = fn()
        except Exception as e:  # noqa: BLE001
            if _is_stop(e):
                return {"stopped": str(e)}
            record_failure(name, name, e)
            return None
        return out

    cur = ckpt.load_patches(ids)
    res = merge_patches(cur, reg, base=base)
    graph = res.graph
    inv_sig = _sig(inv.model_dump_json())
    patches_sig = _sig(*[p.model_dump_json() for p in cur])
    xp: ChunkPatch | None = None
    with audit.span("analyst", "cross_ref"):
        if res.unresolved and not stopped:
            hit = cached_or_run("cross_ref", patches_sig, lambda: resolve_cross_refs(llm, graph, res.unresolved, tools))
            if isinstance(hit, ChunkPatch):
                xp = hit
                ckpt.save_extra("cross_ref", patches_sig, {"patch": xp.model_dump(mode="json")})
            elif isinstance(hit, dict) and "patch" in hit:
                xp = ChunkPatch.model_validate(hit["patch"])
            elif isinstance(hit, dict) and hit.get("stopped"):
                stopped = hit["stopped"]
            if xp is not None:
                res = merge_patches(cur + [xp], reg, base=base)
                graph = res.graph
    graph = collapse_entity_components(dedupe_fact_endpoints(graph))
    ver = verify_evidence(graph, proj.code)
    graph = ver.graph
    cov = check_coverage(inv, graph)
    if not cov.complete and not stopped:
        gsig = _sig(patches_sig, xp.model_dump_json() if xp else "", inv_sig)
        with audit.span("analyst", "gap_fill"):
            hit = cached_or_run("gap_fill", gsig, lambda: fill_gaps(llm, cov, graph, tools))
            gf: GapFillResult | None = None
            if isinstance(hit, GapFillResult):
                gf = hit
                ckpt.save_extra("gap_fill", gsig, {"result": gf.model_dump(mode="json")})
            elif isinstance(hit, dict) and "result" in hit:
                gf = GapFillResult.model_validate(hit["result"])
            elif isinstance(hit, dict) and hit.get("stopped"):
                stopped = hit["stopped"]
        if gf is not None:
            res = merge_patches(cur + ([xp] if xp else []) + [gf.patch], reg, base=base)
            graph = verify_evidence(collapse_entity_components(dedupe_fact_endpoints(res.graph)), proj.code).graph
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
    failed_chunks = sorted(k for k in failures if k in by_id)
    totals = record_session_cost(proj.audit_dir, usd=meter.usd, tokens_in=meter.tokens_in,
                                 tokens_out=meter.tokens_out, calls=meter.calls,
                                 analyzed=len(plan.pending), reused=len(plan.reuse), incremental=incremental)
    return {"chunks": len(chunks), "nodes": len(graph.nodes), "edges": len(graph.edges),
            "coverage_complete": cov.complete, "unmapped": cov.unmapped, "rejected": len(ver.rejected),
            "stopped": stopped, "reused_chunks": len(plan.reuse), "analyzed_chunks": len(plan.pending),
            "failed_chunks": len(failed_chunks), "failed": failed_chunks,
            "failed_tasks": sorted(k for k in failures if k not in by_id),
            "llm_calls": meter.calls, "session_cost_usd": round(meter.usd, 6),
            "total_cost_usd": totals["usd"]}
