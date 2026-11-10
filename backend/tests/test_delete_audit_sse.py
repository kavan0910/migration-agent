import json
import threading
import time

import pytest
from sqlalchemy import func, inspect, select, text

from app.db import Base
from app.models import AuditLog, UsageEvent
from tests.conftest import User, drain


def _rows_for_project(db, pid):
    """Count rows in EVERY table that has a project_id column (found by reflection, so new tables are covered)."""
    out = {}
    with db.engine.connect() as c:
        for t in Base.metadata.sorted_tables:
            if "project_id" in t.c:
                n = c.execute(select(func.count()).select_from(t).where(t.c.project_id == pid)).scalar()
                out[t.name] = n
    return out


def test_deletion_is_complete(alice, bob, worker, db, store):
    keep = alice.project("keep")
    pid = alice.project("victim")
    alice.post(f"/v1/projects/{pid}/user-docs", files={"file": ("spec.md", b"# s", "text/markdown")})
    alice.start_run(pid)
    drain(worker)
    alice.start_run(pid, type="verify")
    drain(worker)
    alice.post(f"/v1/projects/{pid}/comments", json={"item_id": "COMP-0001", "text": "c"})
    alice.post(f"/v1/projects/{pid}/findings/F-0001/resolution", json={"status": "dismissed"})
    alice.start_run(keep)
    drain(worker)
    before = _rows_for_project(db, pid)
    assert all(before[t] > 0 for t in ("sources", "user_docs", "runs", "artifacts", "comments", "resolutions", "usage_events"))
    assert store.list(f"{alice.wid}/{pid}/")
    with db.session() as s:
        run_ids = list(s.scalars(select(text("id")).select_from(text("runs")).where(text("project_id = :p")), {"p": pid}))
        usd_before = s.scalar(select(func.sum(UsageEvent.usd)).where(UsageEvent.workspace_id == alice.wid))

    assert bob.delete(f"/v1/projects/{pid}").status_code == 404
    assert alice.delete(f"/v1/projects/{pid}").status_code == 204

    assert alice.get(f"/v1/projects/{pid}").status_code == 404
    assert all(v == 0 for v in _rows_for_project(db, pid).values()), _rows_for_project(db, pid)
    assert store.list(f"{alice.wid}/{pid}/") == []
    assert not (store.root / alice.wid / pid).exists()
    with db.session() as s:
        for rid in run_ids:  # nothing references the deleted runs either
            for tbl in ("run_events", "jobs", "usage_events", "artifacts"):
                assert s.execute(text(f"SELECT count(*) FROM {tbl} WHERE run_id = :r"), {"r": rid}).scalar() == 0
        # metering is retained, detached from the project
        assert s.scalar(select(func.sum(UsageEvent.usd)).where(UsageEvent.workspace_id == alice.wid)) == pytest.approx(usd_before)
        a = s.scalars(select(AuditLog).where(AuditLog.action == "project.delete")).one()
        assert a.target == pid and a.actor == alice.id and a.workspace_id == alice.wid and a.meta["runs"] == 2
    # the other project is untouched
    assert alice.get(f"/v1/projects/{keep}/document").status_code == 200
    assert store.list(f"{alice.wid}/{keep}/")
    assert alice.delete(f"/v1/projects/{pid}").status_code == 404


def test_audit_trail(alice, bob, client):
    pid = alice.project()
    alice.add_member(bob, "viewer")
    alice.start_run(pid)
    alice.patch(f"/v1/projects/{pid}", json={"name": "n"})
    alice.delete(f"/v1/projects/{pid}")
    r = alice.get(f"/v1/workspaces/{alice.wid}/audit?limit=100").json()
    actions = [a["action"] for a in r["items"]]
    for a in ("workspace.create", "project.create", "source.upload", "member.add", "run.create", "project.update", "project.delete"):
        assert a in actions, a
    assert r["total"] == len(r["items"])
    assert alice.get(f"/v1/workspaces/{alice.wid}/audit?action=member.add").json()["total"] == 1
    assert bob.get(f"/v1/workspaces/{alice.wid}/audit").status_code == 403  # viewer
    dump = json.dumps(r)
    assert "password" not in dump and "secret" not in dump.lower()
    # audit rows belong to their workspace only
    assert all(a["workspace_id"] == alice.wid for a in r["items"])
    assert bob.get(f"/v1/workspaces/{bob.wid}/audit").json()["total"] >= 1


def test_audit_is_append_only_in_api(app):
    paths = {(r.path, m) for r in app.routes if hasattr(r, "methods") for m in r.methods}
    # (routes are lazily included; check via openapi instead)
    spec = app.openapi()["paths"]
    for p, ops in spec.items():
        if p.endswith("/audit"):
            assert set(ops) == {"get"}


def _read_sse(client, url, headers, max_events=100):
    events, cur = [], {}
    with client.stream("GET", url, headers=headers) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line == "":
                if cur:
                    events.append(cur)
                    if cur.get("event") == "end" or len(events) >= max_events:
                        break
                cur = {}
            elif not line.startswith(":"):
                k, _, v = line.partition(": ")
                cur[k] = v
    return events


def test_sse_replays_and_ends(alice, worker, client):
    pid = alice.project()
    r = alice.start_run(pid)
    drain(worker)
    ev = _read_sse(client, f"/v1/runs/{r['id']}/events", alice.h)
    types = [e["event"] for e in ev]
    assert types[0] == "status" and "progress" in types and "usage" in types and "log" in types and types[-1] == "end"
    ids = [int(e["id"]) for e in ev if "id" in e]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert json.loads(ev[-1]["data"])["status"] == "succeeded"
    last = [e for e in ev if e["event"] == "status"][-1]
    assert json.loads(last["data"])["status"] == "succeeded"
    # resume with ?after= and Last-Event-ID
    cut = ids[3]
    ev2 = _read_sse(client, f"/v1/runs/{r['id']}/events?after={cut}", alice.h)
    assert [int(e["id"]) for e in ev2 if "id" in e][0] == cut + 1
    ev3 = _read_sse(client, f"/v1/runs/{r['id']}/events", {**alice.h, "Last-Event-ID": str(cut)})
    assert [int(e["id"]) for e in ev3 if "id" in e][0] == cut + 1


def test_sse_follows_live_run(alice, worker, client):
    pid = alice.project()
    r = alice.start_run(pid)
    got = {}

    def reader():
        got["ev"] = _read_sse(client, f"/v1/runs/{r['id']}/events", alice.h)

    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.3)
    drain(worker)
    t.join(timeout=15)
    assert not t.is_alive()
    assert got["ev"][-1]["event"] == "end"
    assert any(e["event"] == "progress" for e in got["ev"])


def test_sse_auth(alice, bob, client):
    pid = alice.project()
    r = alice.start_run(pid)
    assert client.get(f"/v1/runs/{r['id']}/events").status_code == 401
    assert bob.get(f"/v1/runs/{r['id']}/events").status_code == 404
