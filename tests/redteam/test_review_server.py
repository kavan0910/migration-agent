"""D-06(e): review server vs DNS rebinding, CSRF, token leakage, stored XSS."""
from __future__ import annotations

import http.client
import json
import re
from pathlib import Path

import pytest

from migration_agent.graph.models import ID_PREFIX, Evidence, Graph, Node
from migration_agent.graph.store import save_snapshot
from migration_agent.project import Project
from migration_agent.review_ui import page as page_mod
from migration_agent.review_ui.server import serve
from migration_agent.verify.models import Finding, FindingsFile

XSS = '</script><script>alert(1)</script><img src=x onerror=alert(2)>'


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    p = Project.init(tmp_path_factory.mktemp("rs") / "p", source="src")
    (p.docs / "understanding.latest.md").write_text(f"# Understanding\n\n## 1. Components\n\n- <a id=\"COMP-0001\"></a>**COMP-0001** {XSS}\n")
    n = Node(id="COMP-0001", type="component", name=XSS, module="m", qualified_symbol=XSS, responsibility=XSS,
             evidence=[Evidence(file="a.js", line_start=1, quote="x")])
    save_snapshot(p.graph_dir, Graph(nodes=[n]))
    (p.root / "feedback" / "findings.json").write_text(FindingsFile(graph_version=1, findings=[
        Finding(id="F-0001", kind="contradiction", title=XSS, detail=XSS, item_ids=["COMP-0001"])]).model_dump_json())
    server, url = serve(p.root)
    token = url.rstrip("/").rsplit("/", 1)[-1]
    yield server, token
    server.stop()


def req(server, method, path, *, host=None, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    h = {"Host": host if host is not None else f"127.0.0.1:{server.port}", **(headers or {})}
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in h.items():
        c.putheader(k, v)
    if body is not None:
        c.putheader("Content-Length", str(len(body)))
    c.endheaders(body if body is not None else None)
    r = c.getresponse()
    data = r.read()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data)
    c.close()
    return out


def post_comment(server, token, *, headers=None, text="hello", ctype="application/json"):
    body = json.dumps({"item_id": "COMP-0001", "text": text}).encode()
    return req(server, "POST", f"/{token}/api/comments", headers={"Content-Type": ctype, **(headers or {})}, body=body)


# ---- binding / rebinding -----------------------------------------------------
def test_binds_loopback_only(srv):
    server, _ = srv
    assert server.server_address[0] == "127.0.0.1"
    with pytest.raises(ValueError):
        serve(Path("."), host="0.0.0.0")


@pytest.mark.parametrize("host", ["evil.example", "evil.example:80", "127.0.0.1", "localhost", "127.0.0.1.evil.example:%d",
                                  "attacker.com:%d", "[::1]:%d", "0.0.0.0:%d", ""])
def test_dns_rebinding_host_header_rejected(srv, host):
    server, token = srv
    host = host.replace("%d", str(server.port)) if "%d" in host else host
    st, _, body = req(server, "GET", f"/{token}/api/state", host=host)
    assert st == 403 and b"findings" not in body


def test_host_localhost_with_port_is_accepted(srv):
    server, token = srv
    st, *_ = req(server, "GET", f"/{token}/api/state", host=f"localhost:{server.port}")
    assert st == 200


# ---- token ---------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/state", "/x/api/state", "/api/state?token=%s", "/?token=%s", "/graph"])
def test_token_required(srv, path):
    server, token = srv
    st, *_ = req(server, "GET", path.replace("%s", token))
    assert st == 403


def test_token_in_header_or_cookie_accepted_wrong_rejected(srv):
    server, token = srv
    assert req(server, "GET", "/api/state", headers={"X-Token": token})[0] == 200
    assert req(server, "GET", "/api/state", headers={"Cookie": f"review_token={token}"})[0] == 200
    assert req(server, "GET", "/api/state", headers={"X-Token": token[:-1] + "x"})[0] == 403
    assert req(server, "GET", "/api/state", headers={"Cookie": f"review_token={token}x"})[0] == 403


def test_path_traversal_rejected(srv):
    server, token = srv
    for p in (f"/{token}/../../etc/passwd", f"/{token}/..%2f..%2fetc/passwd", f"/{token}/a\\..\\b"):
        st, _, body = req(server, "GET", p)
        assert st in (400, 403, 404) and b"root:" not in body


def test_root_page_sets_hardened_cookie(srv):
    server, token = srv
    st, h, _ = req(server, "GET", f"/{token}/")
    assert st == 200
    ck = h["set-cookie"]
    assert "HttpOnly" in ck and "SameSite=Strict" in ck


# ---- CSRF / Origin ---------------------------------------------------------------
@pytest.mark.parametrize("hdr", [
    {"Origin": "http://evil.example"}, {"Origin": "null"}, {"Origin": "http://127.0.0.1:1"},
    {"Origin": "https://127.0.0.1:%d"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
])
def test_cross_origin_post_rejected_and_nothing_written(srv, hdr):
    server, token = srv
    hdr = {k: v.replace("%d", str(server.port)) for k, v in hdr.items()}
    before = json.loads(req(server, "GET", f"/{token}/api/state")[2])["comments"]["comments"]
    st, *_ = post_comment(server, token, headers=hdr, text="CSRF-MARKER")
    assert st == 403
    after = json.loads(req(server, "GET", f"/{token}/api/state")[2])["comments"]["comments"]
    assert after == before and not any("CSRF-MARKER" in c["text"] for c in after)


def test_same_origin_post_accepted(srv):
    server, token = srv
    st, *_ = post_comment(server, token, headers={"Origin": f"http://127.0.0.1:{server.port}", "Sec-Fetch-Site": "same-origin"})
    assert st == 201


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""])
def test_simple_request_content_types_rejected(srv, ctype):
    server, token = srv
    st, *_ = post_comment(server, token, ctype=ctype)
    assert st == 415


def test_oversized_body_and_bad_json_rejected(srv):
    server, token = srv
    big = req(server, "POST", f"/{token}/api/comments", headers={"Content-Type": "application/json"}, body=b"{" + b" " * 70000 + b"}")
    assert big[0] == 413
    bad = req(server, "POST", f"/{token}/api/comments", headers={"Content-Type": "application/json"}, body=b"{nope")
    assert bad[0] == 400


def test_unsupported_methods_and_no_cors(srv):
    server, token = srv
    for m in ("PUT", "DELETE", "PATCH"):
        assert req(server, "GET" if False else m, f"/{token}/api/comments", headers={"Origin": "http://evil.example"})[0] in (403, 405)
    st, h, _ = req(server, "GET", f"/{token}/api/state", headers={"Origin": "http://evil.example"})
    assert not any(k.startswith("access-control-") for k in h)
    st, h, _ = req(server, "OPTIONS", f"/{token}/api/comments")
    assert st >= 400 and not any(k.startswith("access-control-") for k in h)


# ---- headers: referrer/token leakage, CSP ----------------------------------------
@pytest.mark.parametrize("path", ["/", "/api/state", "/graph", "/nope"])
def test_security_headers_on_every_response(srv, path):
    server, token = srv
    st, h, _ = req(server, "GET", f"/{token}{path}")
    assert h["referrer-policy"] == "no-referrer"
    assert h["x-content-type-options"] == "nosniff"
    assert h["cache-control"] == "no-store"
    csp = h["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'self'" in csp and "base-uri 'none'" in csp
    assert "form-action 'none'" in csp
    assert not re.search(r"https?://", csp), "CSP must not allow any external origin"


def test_error_responses_also_carry_no_referrer(srv):
    server, _ = srv
    st, h, _ = req(server, "GET", "/wrong/api/state")
    assert st == 403 and h["referrer-policy"] == "no-referrer"


def test_page_has_no_external_resources_or_links(srv):
    server, token = srv
    _, _, body = req(server, "GET", f"/{token}/")
    html = body.decode()
    assert not re.search(r"""(?:src|href)\s*=\s*["']https?://""", html)
    assert "<form" not in html.lower()


# ---- stored XSS -----------------------------------------------------------------------
def test_stored_xss_comment_is_inert(srv):
    server, token = srv
    st, h, body = post_comment(server, token, text=XSS)
    assert st == 201 and h["content-type"].startswith("application/json")
    st, h, state = req(server, "GET", f"/{token}/api/state")
    assert h["content-type"].startswith("application/json") and h["x-content-type-options"] == "nosniff"
    data = json.loads(state)
    assert data["comments"]["comments"][-1]["text"] == XSS            # kept as data, not markup
    assert "<script>" not in json.dumps(data["sections"])             # server-rendered markup is escaped


def test_page_only_uses_innerhtml_for_server_escaped_sections():
    src = page_mod.render_page("/t")
    assert src.count("innerHTML") == 1 and "sec.html" in src
    assert "document.write" not in src and "eval(" not in src and "insertAdjacentHTML" not in src
    assert "outerHTML" not in src


def test_graph_page_escapes_hostile_node_names(srv):
    server, token = srv
    st, h, body = req(server, "GET", f"/{token}/graph")
    assert st == 200
    html = body.decode()
    assert "<script>alert(1)" not in html and "<img src=x" not in html
    # only the two intended script blocks exist
    assert html.lower().count("<script") <= 3


def test_markdown_rendering_escapes_html(srv):
    server, token = srv
    _, _, state = req(server, "GET", f"/{token}/api/state")
    html = json.dumps(json.loads(state)["sections"])
    assert "<script" not in html and "onerror=alert" not in html.replace("&quot;", "").replace("onerror=alert(2)&gt;", "")
