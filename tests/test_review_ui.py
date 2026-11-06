import http.client
import json
from pathlib import Path

import pytest

from migration_agent.graph.models import ID_PREFIX, Evidence, Graph, Node
from migration_agent.graph.store import save_snapshot
from migration_agent.project import Project
from migration_agent.review_ui.markdown import render_sections
from migration_agent.review_ui.server import serve
from migration_agent.verify.models import CommentsFile, Finding, FindingsFile, ResolutionsFile

EVIL = '<script>alert(1)</script><img src=x onerror=alert(2)>'
DOC = f"""# Understanding: demo

Intro {EVIL} text.

## 1. Components

- <a id="COMP-0001"></a>**COMP-0001** app.js
  - Responsibility: boots <b>x</b>
- <a id="COMP-0002"></a>**COMP-0002** db

## 15. Coverage Matrix

Inventory:

- files: 12
"""


def _node(num, name):
    return Node(id=f"{ID_PREFIX['component']}-{num:04d}", type="component", name=name,
                evidence=[Evidence(file="a.js", line_start=1, quote="x")])


@pytest.fixture
def proj(tmp_path):
    p = Project.init(tmp_path / "p", source="src")
    (p.docs / "understanding.latest.md").write_text(DOC)
    save_snapshot(p.graph_dir, Graph(nodes=[_node(1, "app.js")]))
    save_snapshot(p.graph_dir, Graph(nodes=[_node(1, "app.js"), _node(2, "db")]))
    p.inventory_path.write_text(json.dumps({"files": ["a", "b"], "routes": 3}))
    ff = FindingsFile(graph_version=2, findings=[
        Finding(id="F-0001", kind="contradiction", title=EVIL, detail=EVIL, item_ids=["COMP-0001"])])
    (p.root / "feedback" / "findings.json").write_text(ff.model_dump_json())
    return p


@pytest.fixture
def srv(proj):
    server, url = serve(proj.root)
    yield server, url, proj
    server.stop()


def req(server, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    h = {"Host": host or f"127.0.0.1:{server.port}"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, (bytes, str)) else json.dumps(body)
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r.status, out, r


def base(url):
    return "/" + url.rstrip("/").rsplit("/", 1)[1]


def test_rejects_non_loopback(proj):
    with pytest.raises(ValueError):
        serve(proj.root, host="0.0.0.0")


def test_token_required(srv):
    server, url, _ = srv
    b = base(url)
    assert req(server, "GET", "/")[0] == 403
    assert req(server, "GET", "/api/state")[0] == 403
    assert req(server, "GET", "/wrongtoken/api/state")[0] == 403
    assert req(server, "GET", b + "/api/state")[0] == 200
    assert req(server, "GET", "/api/state", headers={"X-Token": "nope"})[0] == 403
    assert req(server, "GET", "/api/state", headers={"X-Token": b[1:]})[0] == 200
    s, _, r = req(server, "GET", b + "/")
    assert s == 200 and "review_token=" in r.getheader("Set-Cookie")
    cookie = r.getheader("Set-Cookie").split(";")[0]
    assert req(server, "GET", "/api/state", headers={"Cookie": cookie})[0] == 200


def test_wrong_host_rejected(srv):
    server, url, _ = srv
    b = base(url)
    assert req(server, "GET", b + "/api/state", host="evil.example")[0] == 403
    assert req(server, "GET", b + "/api/state", host=f"evil.example:{server.port}")[0] == 403
    assert req(server, "GET", b + "/api/state", host=f"localhost:{server.port}")[0] == 200


def test_page_and_state(srv):
    server, url, _ = srv
    b = base(url)
    s, body, r = req(server, "GET", b + "/")
    assert s == 200 and b"<!doctype html>" in body
    assert b"http://" not in body.replace(b"http://www.w3.org", b"") and b"https://" not in body
    assert "default-src 'none'" in r.getheader("Content-Security-Policy")
    st = json.loads(req(server, "GET", b + "/api/state")[1])
    assert st["versions"] == [1, 2]
    assert "COMP-0002" in st["known_ids"]
    assert st["coverage"]["inventory"] == {"files": 2, "routes": 3}
    assert ["files", "12"] in st["coverage"]["doc"]
    assert st["coverage"]["nodes_by_type"] == {"component": 2}
    assert [s["title"] for s in st["sections"]][:2] == ["Understanding: demo", "1. Components"]


def test_post_comment_and_resolution(srv):
    server, url, proj = srv
    b = base(url)
    s, out, _ = req(server, "POST", b + "/api/comments", {"item_id": "COMP-0002", "text": "hello"})
    assert s == 201
    cf = CommentsFile.model_validate_json((proj.root / "feedback" / "comments.json").read_text())
    assert cf.comments[0].id == "CM-0001" and cf.comments[0].item_id == "COMP-0002"
    req(server, "POST", b + "/api/comments", {"item_id": "COMP-0001", "text": "two"})
    cf = CommentsFile.model_validate_json((proj.root / "feedback" / "comments.json").read_text())
    assert [c.id for c in cf.comments] == ["CM-0001", "CM-0002"]
    s, _, _ = req(server, "POST", b + "/api/resolutions", {"finding_id": "F-0001", "status": "dismissed", "note": "n"})
    assert s == 201
    rf = ResolutionsFile.model_validate_json((proj.root / "feedback" / "resolutions.json").read_text())
    assert rf.resolutions[0].status == "dismissed" and rf.resolutions[0].created
    assert not list((proj.root / "feedback").glob(".tmp-*"))
    st = json.loads(req(server, "GET", b + "/api/state")[1])
    assert len(st["comments"]["comments"]) == 2 and len(st["resolutions"]["resolutions"]) == 1


def test_post_validation(srv):
    server, url, proj = srv
    b = base(url)
    P = b + "/api/comments"
    assert req(server, "POST", P, {"item_id": "COMP-9999", "text": "x"})[0] == 404
    assert req(server, "POST", P, {"item_id": "COMP-0001", "text": "  "})[0] == 400
    assert req(server, "POST", P, {"item_id": "COMP-0001"})[0] == 400
    assert req(server, "POST", P, "not json")[0] == 400
    assert req(server, "POST", P, "[1]")[0] == 400
    assert req(server, "POST", P, {"item_id": "COMP-0001", "text": "x" * 6000})[0] == 400
    assert req(server, "POST", P, b"x" * 70000)[0] == 413
    assert req(server, "POST", P, "{}", headers={"Content-Type": "text/plain"})[0] == 415
    R = b + "/api/resolutions"
    assert req(server, "POST", R, {"finding_id": "F-0099", "status": "open"})[0] == 404
    assert req(server, "POST", R, {"finding_id": "F-0001", "status": "bogus"})[0] == 400
    assert req(server, "POST", b + "/api/other", {})[0] == 404
    assert req(server, "PUT", P, {})[0] == 405
    assert not (proj.root / "feedback" / "comments.json").exists()
    assert not (proj.root / "feedback" / "resolutions.json").exists()


def test_cross_origin_post_rejected(srv):
    server, url, proj = srv
    b = base(url)
    body = {"item_id": "COMP-0001", "text": "x"}
    assert req(server, "POST", b + "/api/comments", body, headers={"Origin": "http://evil.example"})[0] == 403
    assert req(server, "POST", b + "/api/comments", body, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    ok = {"Origin": f"http://127.0.0.1:{server.port}"}
    assert req(server, "POST", b + "/api/comments", body, headers=ok)[0] == 201


def test_xss_escaped(srv):
    server, url, _ = srv
    b = base(url)
    req(server, "POST", b + "/api/comments", {"item_id": "COMP-0001", "text": EVIL})
    st = json.loads(req(server, "GET", b + "/api/state")[1])
    # server-rendered doc HTML carries no raw tags from the document
    blob = json.dumps(st["sections"])
    assert "<script" not in blob and "<img" not in blob and "<b>" not in blob
    assert "&lt;script&gt;" in blob
    # dynamic text only travels as JSON data; the page never uses innerHTML for it
    page = req(server, "GET", b + "/")[1].decode()
    assert EVIL not in page
    assert page.count("innerHTML") == 1  # only the pre-escaped doc section
    # JSON is not served as HTML
    _, _, r = req(server, "GET", b + "/api/state")
    assert r.getheader("Content-Type").startswith("application/json")
    assert r.getheader("X-Content-Type-Options") == "nosniff"


def test_markdown_renderer_safety():
    secs = render_sections('## T\n\n[x](javascript:alert(1)) <a id="COMP-0001"></a> `<i>` **b** COMP-0002\n\n```\n<script>\n```\n')
    h = secs[0]["html"]
    assert "<script>" not in h and "<i>" not in h and "href" not in h
    assert 'data-anchor="COMP-0001"' in h and 'data-id="COMP-0002"' in h
    assert "<strong>b</strong>" in h


def test_traversal_and_unknown_routes(srv):
    server, url, _ = srv
    b = base(url)
    for p in ["/../project.json", b + "/../project.json", b + "/%2e%2e/project.json", b + "/..%2fproject.json",
              b + "/docs/understanding.latest.md", b + "/project.json", b + "/graph/../../project.json",
              b + "/%5c..%5cproject.json", b + "/api/state%00"]:
        s, body, _ = req(server, "GET", p)
        assert s in (400, 403, 404), p
        assert b"project.json" not in body or s != 200
    assert req(server, "GET", b + "/graph?v=999")[0] == 404
    assert req(server, "GET", b + "/graph?v=../x")[0] == 400


def test_graph_and_diff(srv):
    server, url, _ = srv
    b = base(url)
    s, body, _ = req(server, "GET", b + "/graph")
    assert s == 200 and b"<canvas" in body
    s, body, _ = req(server, "GET", b + "/graph?v=2&prev=1")
    assert s == 200
    d = json.loads(req(server, "GET", b + "/api/diff?a=1&b=2")[1])
    assert d["added_nodes"] == [{"id": "COMP-0002", "name": "db"}] and d["removed_nodes"] == []
    assert req(server, "GET", b + "/api/diff?a=1&b=9")[0] == 404
    assert req(server, "GET", b + "/api/diff?a=1")[0] == 400


def test_empty_project_and_shutdown(tmp_path):
    p = Project.init(tmp_path / "e", source="s")
    server, url = serve(p.root)
    b = base(url)
    st = json.loads(req(server, "GET", b + "/api/state")[1])
    assert st["findings"]["findings"] == [] and st["sections"] == [] and st["versions"] == []
    assert req(server, "GET", b + "/graph")[0] == 404
    port = server.port
    server.stop()
    assert not server.thread.is_alive()
    with pytest.raises(OSError):
        http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/")


def test_corrupt_feedback_not_overwritten(srv):
    server, url, proj = srv
    b = base(url)
    f = proj.root / "feedback" / "comments.json"
    f.write_text("{garbage")
    s, _, _ = req(server, "POST", b + "/api/comments", {"item_id": "COMP-0001", "text": "x"})
    assert s in (400, 500)
    assert f.read_text() == "{garbage"
    st = json.loads(req(server, "GET", b + "/api/state")[1])
    assert st["errors"]
