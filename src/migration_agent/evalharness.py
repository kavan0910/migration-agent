"""Evaluation harness: score an extracted graph (plain JSON) against hand-labelled expectations.

Actual graph: {"nodes": [{type,name,module,qualified_symbol,[id],[evidence]}], "edges": [{source,target,type}]}.
Expected: see eval/README.md. Identity key of a node is ``type|module|qualified_symbol``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CATEGORIES = ("components", "endpoints", "entities", "edges", "rules")
_NODE_TYPES = {"components": "component", "endpoints": "endpoint", "entities": "entity", "rules": "rule"}
EVAL_REPOS = Path(__file__).resolve().parents[2] / "eval" / "repos"


_EDGE_ALIASES = {"persists": "writes", "relates_to": "depends_on"}
_FILE_LEVEL_OK = {"component", "config", "external_dependency"}
_TYPE_ALIASES = {"data_entity": "entity", "business_rule": "rule"}


def node_key(n: dict) -> str:
    t = n.get("type")
    return f"{_TYPE_ALIASES.get(t, t)}|{n.get('module')}|{n.get('qualified_symbol')}"


def load_actual(graph_dir: Path | str) -> dict:
    """Load graph/latest.json, following its pointer to the snapshot; map to {nodes, edges}."""
    graph_dir = Path(graph_dir)
    data = json.loads((graph_dir / "latest.json").read_text())
    if "nodes" not in data and "path" in data:
        data = json.loads((graph_dir / data["path"]).read_text())
    return data


@dataclass
class CategoryScore:
    precision: float
    recall: float
    f1: float
    expected: int
    actual: int
    missed: list[str] = field(default_factory=list)
    spurious: list[str] = field(default_factory=list)


@dataclass
class Report:
    categories: dict[str, CategoryScore]
    evidence_validity: float
    evidence_errors: list[str]
    injection_hits: list[str]

    @property
    def gates(self) -> dict[str, bool]:
        return {"evidence_validity": self.evidence_validity == 1.0, "injection": not self.injection_hits}

    @property
    def passed(self) -> bool:
        return all(self.gates.values())

    def f1s(self) -> dict[str, float]:
        return {k: v.f1 for k, v in self.categories.items()}

    def to_dict(self) -> dict:
        return {
            "categories": {k: vars(v) for k, v in self.categories.items()},
            "evidence_validity": self.evidence_validity,
            "evidence_errors": self.evidence_errors,
            "injection_hits": self.injection_hits,
            "gates": self.gates,
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Report:
        return cls({k: CategoryScore(**v) for k, v in d["categories"].items()},
                   d["evidence_validity"], d["evidence_errors"], d["injection_hits"])


def _prf(exp: set[str], act: set[str]) -> CategoryScore:
    tp = len(exp & act)
    if not exp and not act:
        p = r = f = 1.0
    else:
        p = tp / len(act) if act else 0.0
        r = tp / len(exp) if exp else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
    return CategoryScore(p, r, f, len(exp), len(act), sorted(exp - act), sorted(act - exp))


def _evidence_items(nodes: list[dict]) -> list[tuple[str, dict]]:
    return [(node_key(n), ev) for n in nodes for ev in n.get("evidence") or []]


def check_evidence(items: list[tuple[str, dict]], repo_root: Path) -> tuple[float, list[str]]:
    """Each item is (owner, {file,start_line,end_line}); valid if file exists and range is within it."""
    errors: list[str] = []
    for owner, ev in items:
        try:
            f = (repo_root / ev["file"]).resolve()
            if not f.is_relative_to(repo_root.resolve()) or not f.is_file():
                errors.append(f"{owner}: missing file {ev['file']}")
                continue
            rng = _ev_range(ev)
            if rng is None:
                # file-level evidence is fine for structural facts (a file is a component), not for rules/endpoints
                if owner.split("|", 1)[0] in _FILE_LEVEL_OK:
                    continue
                raise ValueError("missing line range")
            s, e = rng
            n = len(f.read_text(errors="replace").splitlines())
            if not (1 <= s <= e <= n):
                errors.append(f"{owner}: {ev['file']}:{s}-{e} outside 1..{n}")
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(f"{owner}: malformed evidence ({exc!r})")
    valid = len(items) - len(errors)
    return (valid / len(items) if items else 1.0), errors


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _ev_range(ev: dict) -> tuple[int, int] | None:
    s = ev.get("start_line", ev.get("line_start"))
    if s is None:
        return None
    return int(s), int(ev.get("end_line", ev.get("line_end")) or s)


def _overlap(a: dict, b: dict) -> bool:
    for x in a.get("evidence") or []:
        for y in b.get("evidence") or []:
            if x.get("file") == y.get("file") and (rx := _ev_range(x)) and (ry := _ev_range(y)) \
                    and rx[0] <= ry[1] and ry[0] <= rx[1]:
                return True
    return False


def _canon(n: dict, expected_rules: list[dict]) -> str:
    """Scoring identity: components/entities by defining file, endpoints by file+'METHOD path',
    rules by file+evidence overlap with an expected rule (rule naming is free-form for a model)."""
    t = _TYPE_ALIASES.get(n.get("type"), n.get("type"))
    mod = n.get("module")
    if t in ("component", "entity"):
        return f"{t}|{mod}"
    if t == "rule":
        for r in expected_rules:
            if r.get("module") == mod and _overlap(n, r):
                return node_key(r)
    return f"{t}|{mod}|{n.get('qualified_symbol')}"


def score(actual: dict, expected: dict, repo_root: Path | str | None = None) -> Report:
    root = Path(repo_root) if repo_root else EVAL_REPOS / expected["repo"]
    nodes = actual.get("nodes", [])
    exp_rules = expected.get("rules", [])
    id_to_key = {n["id"]: _canon(n, exp_rules) for n in nodes if "id" in n}

    def key_of_str(k: str) -> str:
        if k.count("|") < 2:
            return k
        t, _, rest = k.partition("|")
        mod, _, sym = rest.partition("|")
        return _canon({"type": t, "module": mod, "qualified_symbol": sym}, exp_rules)

    cats: dict[str, CategoryScore] = {}
    for cat, t in _NODE_TYPES.items():
        exp = {_canon(n, exp_rules) for n in expected.get(cat, [])}
        act = {_canon(n, exp_rules) for n in nodes if _TYPE_ALIASES.get(n.get("type"), n.get("type")) == t}
        cats[cat] = _prf(exp, act)

    def ekey(e: dict) -> str:
        s, tg = e.get("source"), e.get("target")
        return f"{_EDGE_ALIASES.get(e.get('type'), e.get('type'))}|{id_to_key.get(s) or key_of_str(s)}|{id_to_key.get(tg) or key_of_str(tg)}"

    def structural(e: dict) -> bool:
        return not any((id_to_key.get(x) or x).startswith("rule|") for x in (e.get("source"), e.get("target")))

    cats["edges"] = _prf({ekey(e) for e in expected.get("edges", [])},
                         {ekey(e) for e in actual.get("edges", []) if structural(e)})

    validity, errors = check_evidence(_evidence_items(nodes), root)

    needles = [s.lower() for s in expected.get("must_not_appear", [])]
    hits: list[str] = []
    for item in [*nodes, *actual.get("edges", [])]:
        blob = "\n".join(_strings(item)).lower()
        for needle in needles:
            if needle in blob:
                hits.append(f"{needle!r} in {node_key(item) if 'type' in item and 'module' in item else item}")
    return Report(cats, validity, errors, hits)


def load_expected(repo: str) -> dict:
    return json.loads((EVAL_REPOS / repo / "expected.json").read_text())


def expected_evidence(expected: dict) -> list[tuple[str, dict]]:
    return _evidence_items(expected.get("rules", []))


def compare(old: Report | dict, new: Report | dict, tolerance: float = 1e-9) -> tuple[bool, list[str]]:
    """Regression check: (ok, messages). Fails if any category F1 drops or a gate newly fails."""
    o = old if isinstance(old, Report) else Report.from_dict(old)
    n = new if isinstance(new, Report) else Report.from_dict(new)
    problems = []
    for cat, sc in o.categories.items():
        new_f1 = n.categories[cat].f1 if cat in n.categories else 0.0
        if new_f1 < sc.f1 - tolerance:
            problems.append(f"{cat}: F1 dropped {sc.f1:.3f} -> {new_f1:.3f}")
    for gate, ok in n.gates.items():
        if not ok:
            problems.append(f"gate failed: {gate}")
    return (not problems), problems
