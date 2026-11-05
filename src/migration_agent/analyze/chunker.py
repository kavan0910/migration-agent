"""Chunk planner: split a codebase into context-sized, dependency-ordered chunks."""

from __future__ import annotations

import heapq
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from migration_agent.extract.treesitter import AstFacts, FileFacts

LINE_FMT = "{:>5}| {}"
_LINE_OVERHEAD = 8  # "%5d| " + newline


class Chunk(BaseModel):
    id: str
    module: str
    files: list[str]
    est_tokens: int
    depends_on_chunks: list[str] = Field(default_factory=list)
    order: int
    partial_file: bool = False
    # path -> [[start_line, end_line], ...] (1-based, inclusive) actually included
    ranges: dict[str, list[list[int]]] = Field(default_factory=dict)


@dataclass(frozen=True)
class _Seg:
    path: str
    a: int
    b: int
    cost: int
    partial: bool


def _header(path: str, a: int, b: int) -> str:
    return f"=== {path} (lines {a}-{b}) ==="


def _read_lines(code_dir: Path, rel: str) -> list[str] | None:
    try:
        return (code_dir / rel).read_text(encoding="utf8", errors="replace").splitlines()
    except OSError:
        return None


def _tokens(chars: int) -> int:
    return math.ceil(chars / 4) + 1


def _line_costs(lines: list[str]) -> list[int]:
    pre = [0]
    for ln in lines:
        pre.append(pre[-1] + len(ln) + _LINE_OVERHEAD)
    return pre


def _seg_cost(path: str, a: int, b: int, pre: list[int]) -> int:
    return _tokens(len(_header(path, a, b)) + 1 + pre[b] - pre[a - 1])


def _top_level_starts(ff: FileFacts) -> list[int]:
    syms = [(c.start_line, c.end_line) for c in ff.classes]
    syms += [(f.start_line, f.end_line) for f in ff.functions]
    syms.sort(key=lambda t: (t[0], -t[1]))
    starts: list[int] = []
    end = 0
    for s, e in syms:
        if s > end:
            starts.append(s)
            end = e
        else:
            end = max(end, e)
    return sorted(set(starts))


def _split_file(ff: FileFacts, lines: list[str], pre: list[int], budget: int) -> list[_Seg]:
    n = len(lines)
    path = ff.path
    cuts = sorted({s - 1 for s in _top_level_starts(ff) if 1 < s <= n} | {n})
    out: list[_Seg] = []
    s = 1
    while s <= n:
        e = None
        for c in cuts:
            if c >= s and _seg_cost(path, s, c, pre) <= budget:
                e = c
        if e is None:  # a symbol bigger than the budget: split by lines
            e = s
            while e < n and _seg_cost(path, s, e + 1, pre) <= budget:
                e += 1
        out.append(_Seg(path, s, e, _seg_cost(path, s, e, pre), True))
        s = e + 1
    return out


@dataclass
class _Unit:
    module: str
    segs: list[_Seg]

    @property
    def cost(self) -> int:
        return sum(s.cost for s in self.segs)


def _dir_of(path: str) -> str:
    d = str(PurePosixPath(path).parent)
    return d


def _plan_dir(d: str, files_by_dir: dict[str, list[str]], subdirs: dict[str, list[str]],
              segs_of: dict[str, list[_Seg]], budget: int) -> list[_Unit]:
    def subtree_files(x: str) -> list[str]:
        out = list(files_by_dir.get(x, []))
        for s in subdirs.get(x, []):
            out += subtree_files(s)
        return out

    all_files = subtree_files(d)
    total = sum(s.cost for f in all_files for s in segs_of[f])
    if total <= budget and all(not segs_of[f][0].partial for f in all_files):
        return [_Unit(d, [s for f in sorted(all_files) for s in segs_of[f]])]
    items: list[_Unit] = []
    for f in sorted(files_by_dir.get(d, [])):
        for s in segs_of[f]:
            items.append(_Unit(d, [s]))
    for sd in sorted(subdirs.get(d, [])):
        items += _plan_dir(sd, files_by_dir, subdirs, segs_of, budget)
    # pack adjacent units of the same module greedily (never across directories)
    packed: list[_Unit] = []
    for u in items:
        if packed and packed[-1].module == u.module and packed[-1].cost + u.cost <= budget:
            packed[-1] = _Unit(u.module, packed[-1].segs + u.segs)
        else:
            packed.append(u)
    return packed


# ---- import resolution ----------------------------------------------------
_JS_EXTS = (".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs")
_JS_FROM = re.compile(r"""(?:from|import)\s*\(?\s*['"]([^'"]+)['"]""")
_JS_REQ = re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)""")


def _norm(p: str) -> str | None:
    parts: list[str] = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts)


def _resolve_imports(code_dir: Path, ast: AstFacts, paths: set[str]) -> dict[str, set[str]]:
    deps: dict[str, set[str]] = {p: set() for p in paths}
    py_mod: dict[str, str] = {}
    java_mod: dict[str, str] = {}
    go_dirs: dict[str, list[str]] = defaultdict(list)
    for p in sorted(paths):
        pp = PurePosixPath(p)
        if pp.suffix == ".py":
            parts = list(pp.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                py_mod[".".join(parts)] = p
        elif pp.suffix == ".java":
            java_mod[".".join(pp.with_suffix("").parts)] = p
        elif pp.suffix == ".go":
            go_dirs[str(pp.parent)].append(p)

    def suffix_lookup(table: dict[str, str], name: str) -> str | None:
        if name in table:
            return table[name]
        hits = [v for k, v in table.items() if k.endswith("." + name)]
        return hits[0] if len(hits) == 1 else None

    def js_resolve(base_dir: str, spec: str) -> str | None:
        n = _norm(f"{base_dir}/{spec}" if base_dir != "." else spec)
        if n is None:
            return None
        cands = [n] + [n + e for e in _JS_EXTS] + [f"{n}/index{e}" for e in _JS_EXTS]
        return next((c for c in cands if c in paths), None)

    for ff in ast.files:
        p = ff.path
        if p not in paths:
            continue
        pp = PurePosixPath(p)
        d = str(pp.parent)
        found: set[str] = set()
        if ff.language == "python":
            for stmt in ff.imports:
                m = re.match(r"from\s+(\.*)([\w.]*)\s+import\s+(.*)$", stmt)
                if m:
                    dots, mod, names = m.groups()
                    names = [x.strip().split(" as ")[0].strip("() ") for x in names.split(",")]
                    if dots:
                        base = list(pp.parent.parts)
                        base = base[: len(base) - (len(dots) - 1)] if len(dots) > 1 else base
                        prefix = ".".join(base + ([mod] if mod else []))
                        cand = [prefix] + [f"{prefix}.{n}" if prefix else n for n in names]
                        for c in cand:
                            if c in py_mod:
                                found.add(py_mod[c])
                    else:
                        for c in [mod] + [f"{mod}.{n}" for n in names]:
                            t = suffix_lookup(py_mod, c)
                            if t:
                                found.add(t)
                    continue
                m = re.match(r"import\s+(.*)$", stmt)
                if m:
                    for part in m.group(1).split(","):
                        t = suffix_lookup(py_mod, part.strip().split(" as ")[0].strip())
                        if t:
                            found.add(t)
        elif ff.language in ("javascript", "typescript"):
            specs = set()
            for stmt in ff.imports:
                specs.update(_JS_FROM.findall(stmt))
            try:
                text = (code_dir / p).read_text(encoding="utf8", errors="replace")
                specs.update(_JS_REQ.findall(text))
            except OSError:
                pass
            for s in specs:
                if s.startswith("."):
                    t = js_resolve(d, s)
                    if t:
                        found.add(t)
        elif ff.language == "java":
            for stmt in ff.imports:
                m = re.match(r"import\s+(?:static\s+)?([\w.]+)(\.\*)?\s*$", stmt)
                if not m:
                    continue
                name, wild = m.group(1), m.group(2)
                if wild:
                    found.update(v for k, v in java_mod.items()
                                 if k.rsplit(".", 1)[0].endswith(name) and k != name)
                    continue
                parts = name.split(".")
                for i in range(len(parts), 0, -1):  # trim static member names
                    t = suffix_lookup(java_mod, ".".join(parts[:i]))
                    if t:
                        found.add(t)
                        break
        elif ff.language == "go":
            for stmt in ff.imports:
                m = re.search(r'"([^"]+)"', stmt)
                if not m:
                    continue
                spec = m.group(1)
                best, best_k = None, 0
                sparts = spec.split("/")
                for gd in sorted(go_dirs):
                    if gd == ".":
                        continue
                    gparts = gd.split("/")
                    # longest trailing portion of the dir path that ends the import path
                    for k in range(min(len(gparts), len(sparts)), 0, -1):
                        if gparts[-k:] == sparts[-k:]:
                            if k > best_k:
                                best, best_k = gd, k
                            break
                if best and best != d:
                    found.update(go_dirs[best])
        found.discard(p)
        deps[p] = found
    return deps


# ---- ordering ---------------------------------------------------------------
def _scc(nodes: list[int], adj: dict[int, list[int]]) -> list[list[int]]:
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on: set[int] = set()
    stack: list[int] = []
    out: list[list[int]] = []
    counter = [0]

    def strong(v: int) -> None:
        # iterative Tarjan
        work = [(v, 0)]
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on.add(v)
        while work:
            node, i = work.pop()
            nbrs = adj.get(node, [])
            if i < len(nbrs):
                work.append((node, i + 1))
                w = nbrs[i]
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, 0))
                elif w in on:
                    low[node] = min(low[node], index[w])
            else:
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    out.append(sorted(comp))

    for v in nodes:
        if v not in index:
            strong(v)
    return out


def plan_chunks(code_dir: Path, ast: AstFacts, *, context_tokens: int = 200_000,
                budget_fraction: float = 0.6, reserve_tokens: int = 8_000) -> list[Chunk]:
    code_dir = Path(code_dir)
    budget = int(context_tokens * budget_fraction) - reserve_tokens
    if budget <= 0:
        raise ValueError("chunk budget must be positive")

    facts: dict[str, FileFacts] = {}
    segs_of: dict[str, list[_Seg]] = {}
    for ff in sorted(ast.files, key=lambda f: f.path):
        lines = _read_lines(code_dir, ff.path)
        if lines is None:
            continue
        facts[ff.path] = ff
        pre = _line_costs(lines)
        n = max(len(lines), 1)
        if not lines:
            segs_of[ff.path] = [_Seg(ff.path, 1, 1, _seg_cost(ff.path, 1, 1, [0, _LINE_OVERHEAD]), False)]
            continue
        whole = _seg_cost(ff.path, 1, n, pre)
        segs_of[ff.path] = ([_Seg(ff.path, 1, n, whole, False)] if whole <= budget
                            else _split_file(ff, lines, pre, budget))

    files_by_dir: dict[str, list[str]] = defaultdict(list)
    subdirs: dict[str, set[str]] = defaultdict(set)
    for p in facts:
        d = _dir_of(p)
        files_by_dir[d].append(p)
        while d != ".":
            parent = _dir_of(d)
            subdirs[parent].add(d)
            d = parent
    sub = {k: sorted(v) for k, v in subdirs.items()}
    units = _plan_dir(".", files_by_dir, sub, segs_of, budget) if facts else []

    # provisional chunks
    keyed = sorted(units, key=lambda u: (u.segs[0].path, u.segs[0].a))
    file_to_units: dict[str, set[int]] = defaultdict(set)
    for i, u in enumerate(keyed):
        for s in u.segs:
            file_to_units[s.path].add(i)
    fdeps = _resolve_imports(code_dir, ast, set(facts))
    adj_set: dict[int, set[int]] = defaultdict(set)  # i depends on j
    for i, u in enumerate(keyed):
        for s in u.segs:
            for dep in fdeps.get(s.path, ()):
                for j in file_to_units.get(dep, ()):
                    if j != i:
                        adj_set[i].add(j)
    adj = {i: sorted(v) for i, v in adj_set.items()}

    comps = _scc(list(range(len(keyed))), adj)
    comp_of = {v: ci for ci, c in enumerate(comps) for v in c}
    cdeps: dict[int, set[int]] = defaultdict(set)
    rdeps: dict[int, set[int]] = defaultdict(set)
    for i, js in adj.items():
        for j in js:
            if comp_of[i] != comp_of[j]:
                cdeps[comp_of[i]].add(comp_of[j])
                rdeps[comp_of[j]].add(comp_of[i])
    remaining = {c: len(cdeps.get(c, ())) for c in range(len(comps))}
    heap = [(comps[c][0], c) for c in remaining if remaining[c] == 0]
    heapq.heapify(heap)
    seq: list[int] = []
    while heap:
        _, c = heapq.heappop(heap)
        seq.extend(comps[c])
        for r in sorted(rdeps.get(c, ())):
            remaining[r] -= 1
            if remaining[r] == 0:
                heapq.heappush(heap, (comps[r][0], r))

    pos = {u: k for k, u in enumerate(seq)}
    chunks: list[Chunk] = []
    for k, ui in enumerate(seq):
        u = keyed[ui]
        ranges: dict[str, list[list[int]]] = {}
        for s in sorted(u.segs, key=lambda s: (s.path, s.a)):
            ranges.setdefault(s.path, []).append([s.a, s.b])
        deps = sorted(f"CH-{pos[j] + 1:04d}" for j in adj.get(ui, ()) if pos[j] < k)
        chunks.append(Chunk(
            id=f"CH-{k + 1:04d}", module=u.module, files=list(ranges), est_tokens=u.cost,
            depends_on_chunks=deps, order=k + 1,
            partial_file=any(s.partial for s in u.segs), ranges=ranges))
    return chunks


def render_chunk_text(code_dir: Path, chunk: Chunk) -> str:
    code_dir = Path(code_dir)
    blocks: list[str] = []
    for path in chunk.files:
        lines = _read_lines(code_dir, path)
        if lines is None:
            continue
        rngs = chunk.ranges.get(path) or [[1, max(len(lines), 1)]]
        for a, b in rngs:
            b = min(b, len(lines)) if lines else b
            body = [LINE_FMT.format(i, lines[i - 1]) for i in range(a, b + 1)] if lines else []
            blocks.append("\n".join([_header(path, a, b), *body]))
    return "\n".join(blocks) + "\n"
