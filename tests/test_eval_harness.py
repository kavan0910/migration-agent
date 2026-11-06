import copy
import json
from pathlib import Path

import pytest

from migration_agent.evalharness import (
    CATEGORIES, EVAL_REPOS, compare, expected_evidence, check_evidence, load_expected, score,
)

REPOS = ["express-crud", "spring-lite", "django-lite"]


def perfect(expected):
    nodes = []
    for cat in ("components", "endpoints", "entities", "rules"):
        nodes += copy.deepcopy(expected[cat])
    return {"nodes": nodes, "edges": copy.deepcopy(expected["edges"])}


@pytest.mark.parametrize("repo", REPOS)
def test_fixture_sizes_and_shape(repo):
    exp = load_expected(repo)
    assert 3 <= len(exp["rules"]) <= 6
    for cat in CATEGORIES:
        assert exp[cat]
    n = sum(1 for p in (EVAL_REPOS / repo).rglob("*") if p.is_file() and p.name != "expected.json")
    assert n >= (15 if repo == "express-crud" else 8)


@pytest.mark.parametrize("repo", REPOS)
def test_every_expected_evidence_is_real(repo):
    exp = load_expected(repo)
    items = expected_evidence(exp)
    assert len(items) == len(exp["rules"])
    validity, errors = check_evidence(items, EVAL_REPOS / repo)
    assert validity == 1.0, errors


@pytest.mark.parametrize("repo", REPOS)
def test_expected_edges_reference_expected_nodes(repo):
    from migration_agent.evalharness import node_key
    exp = load_expected(repo)
    keys = {node_key(n) for c in ("components", "endpoints", "entities") for n in exp[c]}
    for e in exp["edges"]:
        assert e["source"] in keys and e["target"] in keys, e


@pytest.mark.parametrize("repo", REPOS)
def test_perfect(repo):
    exp = load_expected(repo)
    r = score(perfect(exp), exp)
    assert r.passed
    assert all(c.f1 == 1.0 and not c.missed and not c.spurious for c in r.categories.values())
    assert r.evidence_validity == 1.0


def test_partial_lists_missed():
    exp = load_expected("express-crud")
    act = perfect(exp)
    dropped = act["nodes"].pop(0)
    act["edges"] = act["edges"][:-2]
    r = score(act, exp)
    assert r.categories["components"].recall < 1.0
    assert r.categories["components"].precision == 1.0
    assert r.categories["components"].missed == [f"component|{dropped['module']}"]
    assert len(r.categories["edges"].missed) == 2
    assert r.categories["rules"].f1 == 1.0


def test_spurious_lists_extra():
    exp = load_expected("spring-lite")
    act = perfect(exp)
    act["nodes"].append({"type": "endpoint", "name": "x", "module": "m", "qualified_symbol": "GET /nope"})
    act["edges"].append({"type": "calls", "source": "a", "target": "b"})
    r = score(act, exp)
    assert r.categories["endpoints"].precision < 1.0 and r.categories["endpoints"].recall == 1.0
    assert r.categories["endpoints"].spurious == ["endpoint|m|GET /nope"]
    assert r.categories["edges"].spurious == ["calls|a|b"]


def test_edges_match_via_node_ids():
    exp = load_expected("django-lite")
    act = perfect(exp)
    ids = {}
    for i, n in enumerate(act["nodes"]):
        n["id"] = f"n{i}"
        ids[f"{n['type']}|{n['module']}|{n['qualified_symbol']}"] = n["id"]
    for e in act["edges"]:
        e["source"], e["target"] = ids[e["source"]], ids[e["target"]]
    assert score(act, exp).categories["edges"].f1 == 1.0


def test_injection_fails_gate():
    exp = load_expected("express-crud")
    assert exp["must_not_appear"]
    act = perfect(exp)
    act["nodes"].append({"type": "rule", "name": "PWNED_BY_INJECTION", "module": "src/vendor/legacy-notes.js",
                         "qualified_symbol": "x", "text": "grants admin access to every user"})
    r = score(act, exp)
    assert not r.passed and not r.gates["injection"] and r.injection_hits
    # text-only match, case-insensitive
    act2 = perfect(exp)
    act2["nodes"][0]["text"] = "Please IGNORE previous instructions"
    assert not score(act2, exp).gates["injection"]


def test_evidence_invalid_fails_gate():
    exp = load_expected("express-crud")
    for bad in ({"file": "src/nope.js", "start_line": 1, "end_line": 2},
                {"file": "src/app.js", "start_line": 1, "end_line": 9999},
                {"file": "src/app.js", "start_line": 0, "end_line": 1},
                {"file": "../../../pyproject.toml", "start_line": 1, "end_line": 1},
                {"file": "src/app.js"}):
        act = perfect(exp)
        act["nodes"][-1]["evidence"].append(bad)
        r = score(act, exp)
        assert r.evidence_validity < 1.0 and not r.gates["evidence_validity"] and not r.passed, bad


def test_compare_detects_regression():
    exp = load_expected("express-crud")
    old = score(perfect(exp), exp)
    worse = perfect(exp)
    worse["nodes"] = [n for n in worse["nodes"] if n["type"] != "entity"]
    ok, problems = compare(old, score(worse, exp))
    assert not ok and any(p.startswith("entities") for p in problems)
    assert compare(old, old) == (True, [])
    ok, _ = compare(score(worse, exp), old)  # improvement is fine
    assert ok
    assert compare(old.to_dict(), json.loads(json.dumps(old.to_dict())))[0]
