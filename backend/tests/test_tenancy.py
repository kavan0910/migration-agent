"""Cross-workspace isolation: user B must get 404 (never 403, never data) on every resource of workspace A."""
import io

import pytest

from tests.conftest import drain, make_zip


@pytest.fixture
def world(alice, bob, worker, client):
    pid = alice.project("secret-proj")
    alice.post(f"/v1/projects/{pid}/user-docs", files={"file": ("spec.md", b"# spec", "text/markdown")})
    run = alice.start_run(pid)
    drain(worker)
    alice.post(f"/v1/projects/{pid}/comments", json={"item_id": "COMP-0001", "text": "hello"})
    # a verify run provides findings
    alice.start_run(pid, type="verify")
    drain(worker)
    bob_pid = bob.project("bobs")
    return {"pid": pid, "rid": run["id"], "wid": alice.wid, "docid": alice.get(f"/v1/projects/{pid}/user-docs").json()["items"][0]["id"],
            "bob_pid": bob_pid}


def _cases(w):
    p, r, wid = w["pid"], w["rid"], w["wid"]
    return [
        ("GET", f"/v1/workspaces/{wid}", None),
        ("GET", f"/v1/workspaces/{wid}/members", None),
        ("POST", f"/v1/workspaces/{wid}/members", {"json": {"email": "bob@example.com", "role": "admin"}}),
        ("DELETE", f"/v1/workspaces/{wid}/members/{w['docid']}", None),
        ("GET", f"/v1/workspaces/{wid}/projects", None),
        ("POST", f"/v1/workspaces/{wid}/projects", {"json": {"name": "intruder"}}),
        ("GET", f"/v1/workspaces/{wid}/usage", None),
        ("GET", f"/v1/workspaces/{wid}/entitlements", None),
        ("GET", f"/v1/workspaces/{wid}/audit", None),
        ("GET", f"/v1/projects/{p}", None),
        ("PATCH", f"/v1/projects/{p}", {"json": {"name": "pwned"}}),
        ("DELETE", f"/v1/projects/{p}", None),
        ("POST", f"/v1/projects/{p}/source", {"files": {"file": ("a.zip", make_zip(), "application/zip")}}),
        ("POST", f"/v1/projects/{p}/source", {"json": {"git_url": "https://github.com/a/b"}}),
        ("GET", f"/v1/projects/{p}/user-docs", None),
        ("POST", f"/v1/projects/{p}/user-docs", {"files": {"file": ("x.md", b"x", "text/markdown")}}),
        ("DELETE", f"/v1/projects/{p}/user-docs/{w['docid']}", None),
        ("POST", f"/v1/projects/{p}/estimate", {"json": {}}),
        ("POST", f"/v1/projects/{p}/runs", {"json": {"type": "analyze"}}),
        ("GET", f"/v1/projects/{p}/runs", None),
        ("GET", f"/v1/runs/{r}", None),
        ("POST", f"/v1/runs/{r}/cancel", None),
        ("GET", f"/v1/runs/{r}/events", None),
        ("GET", f"/v1/projects/{p}/graph", None),
        ("GET", f"/v1/projects/{p}/graph/nodes/COMP-0001", None),
        ("GET", f"/v1/projects/{p}/graph/versions", None),
        ("GET", f"/v1/projects/{p}/graph/diff?a=1&b=1", None),
        ("GET", f"/v1/projects/{p}/document", None),
        ("GET", f"/v1/projects/{p}/changelog", None),
        ("GET", f"/v1/projects/{p}/export?format=md", None),
        ("GET", f"/v1/projects/{p}/findings", None),
        ("POST", f"/v1/projects/{p}/findings/F-0001/resolution", {"json": {"status": "dismissed"}}),
        ("GET", f"/v1/projects/{p}/comments", None),
        ("POST", f"/v1/projects/{p}/comments", {"json": {"item_id": "COMP-0001", "text": "x"}}),
    ]


def test_every_endpoint_is_404_for_other_workspace(world, bob):
    for method, path, kw in _cases(world):
        r = bob.req(method, path, **(kw or {}))
        assert r.status_code == 404, (method, path, r.status_code, r.text[:200])
        assert r.json()["error"]["code"] == "not_found"
        assert "secret-proj" not in r.text


def test_unauthenticated_is_401_everywhere(world, client):
    for method, path, kw in _cases(world):
        r = client.request(method, path, **(kw or {}))
        assert r.status_code == 401, (method, path)


def test_nothing_was_modified_by_intruder(world, alice, bob):
    for method, path, kw in _cases(world):
        bob.req(method, path, **(kw or {}))
    p = alice.get(f"/v1/projects/{world['pid']}").json()
    assert p["name"] == "secret-proj"
    assert alice.get(f"/v1/projects/{world['pid']}/user-docs").json()["total"] == 1
    assert alice.get(f"/v1/projects/{world['pid']}/runs").json()["total"] == 2
    assert alice.get(f"/v1/workspaces/{world['wid']}/members").json()["total"] == 1
    assert alice.get(f"/v1/workspaces/{world['wid']}/projects").json()["total"] == 1


def test_lists_never_leak(world, alice, bob):
    assert bob.get("/v1/workspaces").json()["total"] == 1
    items = bob.get(f"/v1/workspaces/{bob.wid}/projects").json()["items"]
    assert [i["id"] for i in items] == [world["bob_pid"]]
    assert bob.get(f"/v1/workspaces/{bob.wid}/usage").json()["totals"]["usd"] == 0


def test_cannot_create_project_in_foreign_workspace_via_ids(world, bob):
    # path ids that mix workspaces: bob's workspace with alice's project id
    assert bob.get(f"/v1/projects/{world['pid']}").status_code == 404
    assert bob.post(f"/v1/projects/{world['bob_pid']}/findings/F-0001/resolution", json={"status": "dismissed"}).status_code == 404


def test_removed_member_loses_access(alice, bob, client):
    pid = alice.project()
    assert alice.post(f"/v1/workspaces/{alice.wid}/members", json={"email": bob.email, "role": "member"}).status_code == 201
    assert bob.get(f"/v1/projects/{pid}").status_code == 200
    assert alice.delete(f"/v1/workspaces/{alice.wid}/members/{bob.id}").status_code == 204
    assert bob.get(f"/v1/projects/{pid}").status_code == 404


def test_store_keys_are_tenant_prefixed(world, store):
    keys = store.list(f"{world['wid']}/")
    assert keys and all(k.startswith(f"{world['wid']}/{world['pid']}/") for k in keys)
    assert not store.list(f"{world['wid']}x/")
