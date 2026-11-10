import pytest

from tests.conftest import User, drain, make_zip


@pytest.fixture
def team(client, alice):
    """alice=owner; carol=admin, dave=member, erin=viewer in alice's workspace."""
    users = {}
    for name, role in (("carol", "admin"), ("dave", "member"), ("erin", "viewer")):
        u = User(client, f"{name}@example.com")
        assert alice.post(f"/v1/workspaces/{alice.wid}/members", json={"email": u.email, "role": role}).status_code == 201
        users[name] = u
    return users


def test_viewer_is_read_only(alice, team):
    pid = alice.project()
    e = team["erin"]
    assert e.get(f"/v1/projects/{pid}").status_code == 200
    assert e.get(f"/v1/projects/{pid}/runs").status_code == 200
    assert e.get(f"/v1/workspaces/{alice.wid}/usage").status_code == 200
    assert e.post(f"/v1/workspaces/{alice.wid}/projects", json={"name": "x"}).status_code == 403
    assert e.post(f"/v1/projects/{pid}/runs", json={}).status_code == 403
    assert e.post(f"/v1/projects/{pid}/source", files={"file": ("a.zip", make_zip(), "x/y")}).status_code == 403
    assert e.post(f"/v1/projects/{pid}/user-docs", files={"file": ("a.md", b"x", "text/markdown")}).status_code == 403
    assert e.post(f"/v1/projects/{pid}/estimate", json={}).status_code == 403
    assert e.post(f"/v1/projects/{pid}/comments", json={"item_id": "a", "text": "x"}).status_code == 403
    assert e.delete(f"/v1/projects/{pid}").status_code == 403
    assert e.get(f"/v1/workspaces/{alice.wid}/audit").status_code == 403


def test_member_can_work_but_not_administer(alice, team, worker):
    pid = alice.project()
    d = team["dave"]
    assert d.post(f"/v1/projects/{pid}/runs", json={"max_usd": 2}).status_code == 202
    assert d.post(f"/v1/workspaces/{alice.wid}/projects", json={"name": "m"}).status_code == 201
    assert d.patch(f"/v1/projects/{pid}", json={"name": "z"}).status_code == 403
    assert d.delete(f"/v1/projects/{pid}").status_code == 403
    assert d.post(f"/v1/workspaces/{alice.wid}/members", json={"email": "x@example.com", "role": "viewer"}).status_code == 403
    assert d.get(f"/v1/workspaces/{alice.wid}/audit").status_code == 403


def test_admin_rules(alice, team, client):
    c = team["carol"]
    pid = alice.project()
    assert c.get(f"/v1/workspaces/{alice.wid}/audit").status_code == 200
    assert c.patch(f"/v1/projects/{pid}", json={"name": "renamed"}).status_code == 200
    f = User(client, "frank@example.com")
    assert c.post(f"/v1/workspaces/{alice.wid}/members", json={"email": f.email, "role": "admin"}).status_code == 403
    assert c.post(f"/v1/workspaces/{alice.wid}/members", json={"email": f.email, "role": "owner"}).status_code == 403
    assert c.post(f"/v1/workspaces/{alice.wid}/members", json={"email": f.email, "role": "member"}).status_code == 201
    assert c.post(f"/v1/workspaces/{alice.wid}/members", json={"email": f.email, "role": "member"}).status_code == 409
    assert c.delete(f"/v1/workspaces/{alice.wid}/members/{alice.id}").status_code == 403  # cannot remove an owner
    assert c.delete(f"/v1/workspaces/{alice.wid}/members/{f.id}").status_code == 204
    assert c.delete(f"/v1/projects/{pid}").status_code == 204


def test_unknown_user_and_last_owner(alice, team):
    assert alice.post(f"/v1/workspaces/{alice.wid}/members", json={"email": "ghost@example.com", "role": "member"}).status_code == 404
    assert alice.delete(f"/v1/workspaces/{alice.wid}/members/{alice.id}").status_code == 409
    assert alice.post(f"/v1/workspaces/{alice.wid}/members", json={"email": "carol@example.com", "role": "owner"}).status_code == 409  # already member
    assert alice.post(f"/v1/workspaces/{alice.wid}/members", json={"email": "x", "role": "root"}).status_code == 422


def test_member_can_leave(alice, team):
    d = team["dave"]
    assert d.delete(f"/v1/workspaces/{alice.wid}/members/{d.id}").status_code == 204
    assert d.get(f"/v1/workspaces/{alice.wid}").status_code == 404


def test_owner_can_promote_and_second_owner_can_leave(alice, team, client):
    assert alice.post(f"/v1/workspaces/{alice.wid}/members", json={"email": "newbie@example.com", "role": "viewer"}).status_code == 404
    g = User(client, "gina@example.com")
    assert alice.post(f"/v1/workspaces/{alice.wid}/members", json={"email": g.email, "role": "owner"}).status_code == 201
    assert alice.delete(f"/v1/workspaces/{alice.wid}/members/{alice.id}").status_code == 204


def test_workspace_creation_and_listing(alice):
    r = alice.post("/v1/workspaces", json={"name": "Second"})
    assert r.status_code == 201 and r.json()["role"] == "owner"
    assert alice.get("/v1/workspaces").json()["total"] == 2
    assert alice.get("/v1/workspaces?limit=1&offset=1").json()["items"][0]["name"] == "Second"
    assert alice.get("/v1/workspaces?limit=500").status_code == 422
