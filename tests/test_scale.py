"""D-02: large-codebase handling on a synthetic 2400-file repo (mixed py/js/java, deep chains, ~10% cross links)."""
import json
import random
import time
from pathlib import Path

import pytest

from migration_agent.analyze.chunker import _tokens, plan_chunks, render_chunk_text
from migration_agent.analyze.incremental import compute_deps
from migration_agent.analyze.merger import merge_patches
from migration_agent.analyze.patch import ChunkPatch, EdgePatch, NodePatch, NodeRef
from migration_agent.analyze.runner import estimate, run_analyze
from migration_agent.extract.treesitter import extract_ast
from migration_agent.graph import Registry, load_latest, save_snapshot
from migration_agent.graph.models import Evidence
from migration_agent.llm.base import BudgetExceeded
from migration_agent.llm.offline import OfflineLLM

from synth_repo import generate_repo, make_project

N_FILES = 2400
CTX = 30_000                       # -> per-chunk budget of 10_000 tokens (many chunks)
BUDGET = int(CTX * 0.6) - 8_000


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    d = tmp_path_factory.mktemp("scale") / "src"
    files = generate_repo(d, N_FILES)
    return d, files, extract_ast(d)


def test_synthetic_repo_shape(repo):
    d, files, ast = repo
    assert len(files) >= 2000
    assert {f.language for f in ast.files} >= {"python", "javascript", "java"}


def test_chunk_planning_is_bounded_in_time_and_size(repo):
    d, _, ast = repo
    t0 = time.perf_counter()
    chunks = plan_chunks(d, ast, context_tokens=CTX)
    elapsed = time.perf_counter() - t0
    print(f"\nplan_chunks: {len(chunks)} chunks over {len(ast.files)} files in {elapsed:.2f}s")
    assert elapsed < 30
    assert all(c.est_tokens <= BUDGET for c in chunks)
    # the estimate is honest about the real rendered text
    for c in chunks[:5]:
        assert _tokens(len(render_chunk_text(d, c))) <= BUDGET * 1.05
    assert sorted(f for c in chunks for f in c.files) == sorted(f.path for f in ast.files)  # nothing lost/duplicated
    assert [c.order for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(int(dep[3:]) < c.order for c in chunks for dep in c.depends_on_chunks)


def test_related_files_stay_together(repo):
    d, _, ast = repo
    chunks = plan_chunks(d, ast, context_tokens=CTX)
    pkgs: dict[str, set[str]] = {}
    for c in chunks:
        for f in c.files:
            pkgs.setdefault(f.rsplit("/", 1)[0], set()).add(c.id)
    together = sum(len(v) == 1 for v in pkgs.values())
    assert together / len(pkgs) >= 0.9, "an import chain (package) should not be scattered over chunks"


def test_deep_flat_chain_is_split_with_bounded_neighbour_context(tmp_path):
    """2000 files in ONE directory, each importing its predecessor: chunks are budget-bounded, ordered by
    dependency, and prompts carry only a bounded summary of already-processed neighbours."""
    d = tmp_path / "chain"
    d.mkdir()
    for i in range(2000):
        imp = f"from .m{i - 1} import f{i - 1}\n" if i else ""
        (d / f"m{i}.py").write_text(imp + "".join(f"# filler {j}\n" for j in range(20)) + f"def f{i}():\n    return {i}\n")
    ast = extract_ast(d)
    chunks = plan_chunks(d, ast, context_tokens=14_000)  # budget 400 tokens -> many small chunks
    assert len(chunks) > 100 and all(c.est_tokens <= 400 for c in chunks)
    of = {f: c.order for c in chunks for f in c.files}
    for i in range(1, 2000):
        assert of[f"m{i - 1}.py"] <= of[f"m{i}.py"]
    # every chunk after the first points at its predecessor chunk(s)
    assert all(c.depends_on_chunks for c in chunks[1:])


def test_graph_merge_and_snapshot_scale(tmp_path):
    rng = random.Random(1)
    patches = []
    for c in range(120):
        nodes, edges = [], []
        for i in range(60):
            f = f"svc/pkg{c}/m{i}.py"
            for t, sym in (("component", f), ("business_rule", f"{f}#r"), ("endpoint", f"GET /x{c}/{i}")):
                nodes.append(NodePatch(type=t, module=f"svc/pkg{c}", qualified_symbol=sym, name=sym,
                                       responsibility="does a thing " * 5,
                                       evidence=[Evidence(file=f, line_start=1, line_end=3, quote="x = 1")]))
            edges.append(EdgePatch(source=NodeRef(type="component", module=f"svc/pkg{c}", qualified_symbol=f),
                                   target=NodeRef(type="endpoint", module=f"svc/pkg{c}", qualified_symbol=f"GET /x{c}/{i}"),
                                   type="calls", evidence=[Evidence(file=f, line_start=2)]))
            oc = rng.randrange(120)
            edges.append(EdgePatch(source=NodeRef(type="component", module=f"svc/pkg{c}", qualified_symbol=f),
                                   target=NodeRef(type="component", module=f"svc/pkg{oc}", qualified_symbol=f"svc/pkg{oc}/m1.py"),
                                   type="depends_on"))
        patches.append(ChunkPatch(chunk_id=f"CH-{c:04d}", nodes=nodes, edges=edges))
    reg = Registry(tmp_path / "registry.json")
    t0 = time.perf_counter()
    res = merge_patches(patches, reg)
    t_merge = time.perf_counter() - t0
    assert len(res.graph.nodes) == 120 * 60 * 3 and len(res.graph.edges) > 7000
    t0 = time.perf_counter()
    path = save_snapshot(tmp_path / "g", res.graph)
    t_save = time.perf_counter() - t0
    t0 = time.perf_counter()
    g = load_latest(tmp_path / "g")
    t_load = time.perf_counter() - t0
    size_mb = path.stat().st_size / 1e6
    print(f"\nmerge {t_merge:.2f}s save {t_save:.2f}s load {t_load:.2f}s snapshot {size_mb:.1f}MB "
          f"({len(g.nodes)} nodes, {len(g.edges)} edges)")
    assert (len(g.nodes), len(g.edges)) == (len(res.graph.nodes), len(res.graph.edges))
    assert t_merge < 10 and t_save < 10 and t_load < 10
    assert size_mb < 50


def test_full_analysis_at_scale_with_fake_llm(tmp_path):
    proj, _ = make_project(tmp_path, N_FILES)
    llm = OfflineLLM()
    t0 = time.perf_counter()
    out = run_analyze(proj, llm, context_tokens=CTX)
    elapsed = time.perf_counter() - t0
    snap = max((proj.graph_dir / "snapshots").iterdir(), key=lambda p: p.name)
    print(f"\nrun_analyze: {out['chunks']} chunks, {out['nodes']} nodes, {out['edges']} edges, "
          f"{len(llm.calls)} calls, {elapsed:.1f}s, snapshot {snap.stat().st_size / 1e6:.1f}MB")
    assert out["stopped"] is None and out["failed_chunks"] == 0
    assert len(llm.calls) == 2 * out["chunks"]                       # bounded cost: 2 calls per chunk, no more
    assert out["nodes"] >= N_FILES and snap.stat().st_size < 50_000_000
    assert out["coverage_complete"]
    assert (proj.docs / "understanding.latest.md").exists()


def test_estimate_precedes_run_and_bounds_cost(tmp_path):
    proj, _ = make_project(tmp_path, 300, per_pkg=20)
    n, usd = estimate(proj)
    assert n >= 1 and usd > 0
    # runner-level budget: stops gracefully, keeps partial results, renders
    from migration_agent.llm.base import LLMResponse

    class Paid(OfflineLLM):
        def complete(self, **kw):
            r = super().complete(**kw)
            return LLMResponse(text=r.text, model="p", tokens_in=1, tokens_out=1, cost_usd=0.01)

    llm = Paid()
    out = run_analyze(proj, llm, max_usd=0.05, context_tokens=14_000 + 3_000)
    assert out["stopped"] and "budget" in out["stopped"].lower()
    assert len(llm.calls) <= 6 and out["analyzed_chunks"] > 0
    assert out["nodes"] > 0 and (proj.docs / "understanding.latest.md").exists()
    assert out["session_cost_usd"] == pytest.approx(0.01 * len(llm.calls))


def test_llm_budget_exceeded_midway_keeps_partial_results(tmp_path):
    proj, _ = make_project(tmp_path, 300, per_pkg=20)

    class Boom(OfflineLLM):
        def complete(self, **kw):
            if len(self.calls) >= 8:
                raise BudgetExceeded("USD budget: spent too much")
            return super().complete(**kw)

    out = run_analyze(proj, Boom(), context_tokens=17_000)
    assert out["stopped"] and out["nodes"] > 0
    assert 0 < len(list((proj.root / "checkpoint" / "chunks").glob("*.json"))) < out["chunks"]


def test_workers_give_identical_graph_to_serial(tmp_path):
    p1, _ = make_project(tmp_path / "a", 240, per_pkg=12)
    p2, _ = make_project(tmp_path / "b", 240, per_pkg=12)
    o1 = run_analyze(p1, OfflineLLM(), context_tokens=17_000, workers=1)
    o2 = run_analyze(p2, OfflineLLM(), context_tokens=17_000, workers=4)
    assert o1["chunks"] > 3 and (o1["nodes"], o1["edges"]) == (o2["nodes"], o2["edges"])
    g1, g2 = load_latest(p1.graph_dir), load_latest(p2.graph_dir)
    assert [(n.id, n.qualified_symbol) for n in g1.nodes] == [(n.id, n.qualified_symbol) for n in g2.nodes]
    assert [e.id for e in g1.edges] == [e.id for e in g2.edges]


def test_deep_chain_prompts_stay_bounded(tmp_path):
    from migration_agent.pipeline import run_extract, run_ingest
    from migration_agent.project import Project
    src = tmp_path / "chain-src"
    src.mkdir()
    for i in range(300):
        imp = f"from .m{i - 1} import f{i - 1}\n" if i else ""
        (src / f"m{i}.py").write_text(imp + "".join(f"# filler {j}\n" for j in range(20)) + f"def f{i}():\n    return {i}\n")
    proj = Project.init(tmp_path / "chain.migration", str(src), "chain")
    run_ingest(proj, str(src))
    run_extract(proj)

    class Spy(OfflineLLM):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def complete(self, **kw):
            self.prompts.append(kw["prompt"])
            return super().complete(**kw)

    llm = Spy()
    out = run_analyze(proj, llm, context_tokens=14_000)
    assert out["chunks"] > 50 and out["failed_chunks"] == 0
    assert max(len(p) for p in llm.prompts) < 12_000          # context never grows with tree depth
    assert sum("Already analysed neighbouring chunks" in p for p in llm.prompts) > out["chunks"]
