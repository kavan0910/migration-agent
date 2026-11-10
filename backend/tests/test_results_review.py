import json

import pytest
from sqlalchemy import select

from app.models import Comment, Resolution
from tests.conftest import drain


@pytest.fixture
def analysed(alice, worker):
    pid = alice.project()
    alice.start_run(pid)
    drain(worker)
    alice.start_run(pid, type="verify")
    drain(worker)
    return pid


def test_graph_filters_and_paging(alice, analysed):
    g = alice.get(f"/v1/projects/{analysed}/graph").json()
    assert g["version"] == 1 and g["total"] == 3 and len(g["edges"]) == 1
    assert alice.get(f"/v1/projects/{analysed}/graph?type=endpoint").json()["total"] == 1
    assert alice.get(f"/v1/projects/{analysed}/graph?q=BOOK").json()["total"] == 2  # GET /books + Book
    p = alice.get(f"/v1/projects/{analysed}/graph?limit=1&offset=2").json()
    assert len(p["items"]) == 1 and p["total"] == 3
    assert alice.get(f"/v1/projects/{analysed}/graph?version=9").status_code == 404
    assert alice.get(f"/v1/projects/{analysed}/graph?limit=999").status_code == 422
    n = alice.get(f"/v1/projects/{analysed}/graph/nodes/EP-0001").json()
    assert n["node"]["name"] == "GET /books" and n["edges"][0]["target"] == "COMP-0001"
    assert alice.get(f"/v1/projects/{analysed}/graph/nodes/NOPE").status_code == 404


def test_empty_project_results(alice):
    pid = alice.project()
    g = alice.get(f"/v1/projects/{pid}/graph").json()
    assert g["total"] == 0 and g["version"] == 0
    assert alice.get(f"/v1/projects/{pid}/document").status_code == 404
    assert alice.get(f"/v1/projects/{pid}/graph/versions").json()["total"] == 0
    assert alice.get(f"/v1/projects/{pid}/findings").json()["total"] == 0
    assert alice.get(f"/v1/projects/{pid}/export?format=html").status_code == 404


def test_versions_diff_and_reanalysis_flow(alice, worker, analysed):
    r = alice.post(f"/v1/projects/{analysed}/findings/F-0001/resolution", json={"status": "needs_reanalysis", "note": "check"})
    assert r.status_code == 201
    run = r.json()["run"]
    assert run["type"] == "reanalyze" and run["status"] == "queued"
    drain(worker)
    assert alice.get(f"/v1/runs/{run['id']}").json()["status"] == "succeeded"
    vs = alice.get(f"/v1/projects/{analysed}/graph/versions").json()
    assert [v["version"] for v in vs["items"]] == [1, 2]
    d = alice.get(f"/v1/projects/{analysed}/graph/diff?a=1&b=2").json()
    assert [(n["id"], n["name"]) for n in d["added_nodes"]] == [("RULE-0001", "rule v2")] and d["removed_nodes"] == []
    assert alice.get(f"/v1/projects/{analysed}/graph/diff?a=1&b=7").status_code == 404
    assert "reanalyzed" in alice.get(f"/v1/projects/{analysed}/changelog").text
    # engine received the resolution when the run was prepared
    f = {x["id"]: x for x in alice.get(f"/v1/projects/{analysed}/findings").json()["items"]}
    assert f["F-0001"]["resolution"]["status"] == "needs_reanalysis"
    with worker.db.session() as s:
        assert all(x.applied for x in s.scalars(select(Resolution)))


def test_resolution_statuses_and_triggers(alice, analysed):
    u = f"/v1/projects/{analysed}/findings"
    assert alice.post(f"{u}/F-0001/resolution", json={"status": "dismissed"}).json()["run"] is None
    assert alice.post(f"{u}/F-0001/resolution", json={"status": "accepted_code"}).json()["run"] is None
    r = alice.post(f"{u}/F-0002/resolution", json={"status": "accepted_doc"}).json()
    assert r["run"]["type"] == "reanalyze"
    # a second trigger while one is queued coalesces
    r2 = alice.post(f"{u}/F-0001/resolution", json={"status": "needs_reanalysis"}).json()
    assert r2["run"] is None and r2["run_denied_reason"] is None
    assert alice.get(f"/v1/projects/{analysed}/runs?status=queued").json()["total"] == 1
    assert alice.post(f"{u}/F-0001/resolution", json={"status": "bogus"}).status_code == 422
    assert alice.post(f"{u}/F-9999/resolution", json={"status": "dismissed"}).status_code == 404
    by = {x["id"]: x for x in alice.get(f"{u}?status=accepted_doc").json()["items"]}
    assert list(by) == ["F-0002"]
    assert alice.get(f"{u}?kind=contradiction").json()["total"] == 1


def test_resolution_run_denied_by_entitlement_still_records(alice, analysed, db):
    from app.billing import ensure_entitlement
    with db.session() as s:
        ensure_entitlement(s, alice.wid).overrides = {"max_runs_per_month": 0}
    r = alice.post(f"/v1/projects/{analysed}/findings/F-0001/resolution", json={"status": "needs_reanalysis"})
    assert r.status_code == 201 and r.json()["run"] is None and "limit" in r.json()["run_denied_reason"]
    assert alice.get(f"/v1/projects/{analysed}/findings").json()["items"][0]["resolution"]["status"] == "needs_reanalysis"


def test_comments(alice, analysed, worker, fake):
    u = f"/v1/projects/{analysed}/comments"
    r = alice.post(u, json={"item_id": "COMP-0001", "text": "  looks wrong  "})
    assert r.status_code == 201 and r.json()["text"] == "looks wrong"
    assert alice.post(u, json={"item_id": "NOPE-1", "text": "x"}).status_code == 404
    assert alice.post(u, json={"item_id": "COMP-0001", "text": ""}).status_code == 422
    assert alice.get(f"{u}?item_id=COMP-0001").json()["total"] == 1
    assert alice.get(f"{u}?item_id=EP-0001").json()["total"] == 0
    assert alice.get(f"/v1/projects/{analysed}/findings").json()["items"][0]["comment_count"] == 0
    alice.start_run(analysed, type="reanalyze")
    drain(worker)
    with worker.db.session() as s:
        assert s.scalar(select(Comment.applied)) is True


def test_exports(alice, analysed):
    md = alice.get(f"/v1/projects/{analysed}/export?format=md")
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"] and md.text.startswith("# Understanding")
    js = alice.get(f"/v1/projects/{analysed}/export?format=json").json()
    assert js["graph"]["version"] == 1 and len(js["findings"]["findings"]) == 2
    assert alice.get(f"/v1/projects/{analysed}/export?format=pdf").status_code == 422


def test_html_export_is_sandboxed(alice, analysed, store, db):
    from app.materialize import results_key
    from app.models import Project
    with db.session() as s:
        p = s.get(Project, analysed)
        store.put(results_key(p, "graph.html"), b"<script>alert(1)</script>")
    r = alice.get(f"/v1/projects/{analysed}/export?format=html")
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"]


def test_web_facing_shapes(alice, analysed):
    """Fields the web client relies on (docs/platform-contract.md 'Response shapes')."""
    p = alice.get(f"/v1/projects/{analysed}").json()
    assert p["source_kind"] == "upload" and p["last_run_status"] == "succeeded" and p["node_count"] == 3
    assert isinstance(p["open_findings"], int)
    n = alice.get(f"/v1/projects/{analysed}/graph/nodes/EP-0001").json()
    assert [x["id"] for x in n["neighbours"]] == ["COMP-0001"]
    v = alice.get(f"/v1/projects/{analysed}/graph/versions").json()["items"][0]
    assert v["node_count"] == 3 and "edge_count" in v
    # comments may attach to a finding id as well as to graph ids
    assert alice.post(f"/v1/projects/{analysed}/comments", json={"item_id": "F-0001", "text": "discuss"}).status_code == 201
    assert alice.post(f"/v1/projects/{analysed}/comments", json={"item_id": "F-9999", "text": "x"}).status_code == 404
