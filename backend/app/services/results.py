"""Read derived artifacts for the API. Everything is served from the object store (the API never opens customer
code); keys are built from the project row the tenancy dependency already authorised."""
from __future__ import annotations

import json

from ..errors import ApiError
from ..materialize import results_key
from ..models import Project
from ..storage import ObjectStore, StoreError


def read_text(store: ObjectStore, project: Project, name: str) -> str | None:
    try:
        return store.get(results_key(project, name)).decode("utf-8", "replace")
    except StoreError:
        return None


def read_json(store: ObjectStore, project: Project, name: str, default=None):
    t = read_text(store, project, name)
    if t is None:
        return default
    try:
        return json.loads(t)
    except ValueError:
        return default


def versions(store: ObjectStore, project: Project) -> list[dict]:
    return read_json(store, project, "graph/versions.json", []) or []


def snapshot(store: ObjectStore, project: Project, version: int | None) -> dict:
    vs = versions(store, project)
    if not vs:
        if version not in (None, 0):
            raise ApiError(404, "not_found", "unknown version")
        return {"version": 0, "nodes": [], "edges": []}
    nums = sorted(int(v["version"]) for v in vs)
    v = nums[-1] if version is None else int(version)
    if v not in nums:
        raise ApiError(404, "not_found", "unknown version")
    snap = read_json(store, project, f"graph/v{v}.json")
    if snap is None:
        raise ApiError(404, "not_found", "unknown version")
    return snap


def diff(a: dict, b: dict) -> dict:
    """Generic structural diff of two snapshots (node/edge ids)."""
    na = {n["id"]: n for n in a.get("nodes", [])}
    nb = {n["id"]: n for n in b.get("nodes", [])}
    ea = {e["id"]: e for e in a.get("edges", [])}
    eb = {e["id"]: e for e in b.get("edges", [])}
    names = {k: v.get("name", "") for k, v in {**na, **nb}.items()}
    types = {k: v.get("type", "") for k, v in {**na, **nb}.items()}
    row = lambda ids: [{"id": i, "name": names.get(i, ""), "type": types.get(i, "")} for i in sorted(ids)]  # noqa: E731
    return {"a": a.get("version"), "b": b.get("version"),
            "added_nodes": row(set(nb) - set(na)), "removed_nodes": row(set(na) - set(nb)),
            "changed_nodes": row(i for i in set(na) & set(nb) if na[i] != nb[i]),
            "added_edges": len(set(eb) - set(ea)), "removed_edges": len(set(ea) - set(eb)),
            "changed_edges": sum(1 for i in set(ea) & set(eb) if ea[i] != eb[i])}
