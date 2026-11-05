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


def node_key(n: dict) -> str:
    return f"{n.get('type')}|{n.get('module')}|{n.get('qualified_symbol')}"


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
            s, e = int(ev["start_line"]), int(ev.get("end_line", ev["start_line"]))
            if not f.is_relative_to(repo_root.resolve()) or not f.is_file():
                errors.append(f"{owner}: missing file {ev['file']}")
                continue
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


def score(actual: dict, expected: dict, repo_root: Path | str | None = None) -> Report:
    root = Path(repo_root) if repo_root else EVAL_REPOS / expected["repo"]
    nodes = actual.get("nodes", [])
    id_to_key = {n["id"]: node_key(n) for n in nodes if "id" in n}
    cats: dict[str, CategoryScore] = {}
    for cat, t in _NODE_TYPES.items():
        exp = {node_key(n) for n in expected.get(cat, [])}
        act = {node_key(n) for n in nodes if n.get("type") == t}
        cats[cat] = _prf(exp, act)

    def ekey(e: dict) -> str:
        s, tg = e.get("source"), e.get("target")
        return f"{e.get('type')}|{id_to_key.get(s, s)}|{id_to_key.get(tg, tg)}"

    cats["edges"] = _prf({ekey(e) for e in expected.get("edges", [])},
                         {ekey(e) for e in actual.get("edges", [])})

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
