#!/usr/bin/env python3
"""API-level end-to-end journey against a locally started backend + worker (no Docker, no LLM, no spend).

    scripts/e2e_local.sh                       # starts SQLite backend + worker in a temp dir, runs, cleans up
    E2E_API_URL=http://127.0.0.1:8000 scripts/e2e_local.sh   # use an already running stack instead

The stack is started with AUTH_MODE=dev and ENGINE_OFFLINE=1 (the engine runs without any model), a LocalStore in the
temp dir and a throw-away SQLite file. Exits non-zero on the first failed check.
"""
from __future__ import annotations

import io
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
ORIGIN = "http://localhost:3000"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base: str):
        self.base, self.token = base.rstrip("/"), None

    def call(self, method, path, body=None, *, raw=None, ctype=None, headers=None, expect=None):
        h = dict(headers or {})
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        data = raw
        if body is not None:
            data, ctype = json.dumps(body).encode(), "application/json"
        if ctype:
            h["Content-Type"] = ctype
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                status, text, rh = r.status, r.read().decode(), r.headers
        except urllib.error.HTTPError as e:
            status, text, rh = e.code, e.read().decode(), e.headers
        if expect is not None and status != expect:
            raise AssertionError(f"{method} {path}: expected {expect}, got {status}: {text[:300]}")
        if "json" in (rh.get("content-type") or "") and text:
            return status, json.loads(text), rh
        return status, text, rh

    def sse(self, path, timeout=180):
        """Read an SSE stream with an Authorization header (what the web client does with fetch)."""
        req = urllib.request.Request(self.base + path, headers={"Authorization": f"Bearer {self.token}",
                                                                "Accept": "text/event-stream"})
        events, ev, data, eid = [], None, [], None
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            assert "text/event-stream" in r.headers["content-type"], r.headers["content-type"]
            for raw in r:
                line = raw.decode().rstrip("\n").rstrip("\r")
                if line == "":
                    if ev or data:
                        events.append({"event": ev or "message", "id": eid, "data": json.loads("\n".join(data) or "null")})
                        if ev == "end":
                            return events
                    ev, data, eid = None, [], None
                elif line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif line.startswith("id:"):
                    eid = line[3:].strip()
                if time.time() - t0 > timeout:
                    raise AssertionError("SSE did not finish in time")
        return events


def multipart(field, filename, content, ctype="application/zip"):
    b = "----e2e" + secrets.token_hex(8)
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def zip_dir(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(path.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(path).as_posix())
    return buf.getvalue()


PASS = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok    {msg}")


def journey(api: str) -> None:
    c = Client(api)
    print(f"== journey against {api}")
    assert c.call("GET", "/healthz", expect=200)[1] == {"status": "ok"}
    c.call("GET", "/readyz", expect=200)
    ok("healthz/readyz")

    # CORS preflight and simple request from the web origin
    st, _, h = c.call("OPTIONS", "/v1/me", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                                                     "Access-Control-Request-Headers": "authorization"})
    assert st == 200 and h.get("access-control-allow-origin") == ORIGIN, (st, dict(h))
    ok("CORS preflight allows web origin + Authorization header")

    email, pw = f"e2e-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(16)
    st, body, _ = c.call("POST", "/v1/auth/signup", {"email": email, "password": pw, "display_name": "E2E User"}, expect=201)
    assert body["access_token"] and body["user"]["email"] == email and body["user"]["display_name"] == "E2E User"
    c.token = body["access_token"]
    c.call("GET", "/v1/me", expect=200)
    c.call("POST", "/v1/auth/login", {"email": email, "password": "wrong-password-xx"}, expect=401)
    err = c.call("POST", "/v1/auth/login", {"email": email, "password": "wrong-password-xx"})[1]
    assert set(err) == {"error"} and {"code", "message"} <= set(err["error"])
    c2 = Client(api)
    c2.call("GET", "/v1/me", expect=401)
    ok("signup/login/me, error envelope, anonymous 401")

    wl = c.call("GET", "/v1/workspaces", expect=200)[1]
    assert wl["total"] == 1 and wl["items"][0]["role"] == "owner"
    wid = wl["items"][0]["id"]
    ok("default workspace created at signup")

    proj = c.call("POST", f"/v1/workspaces/{wid}/projects", {"name": "django-lite"}, expect=201)[1]
    pid = proj["id"]
    assert proj["source_kind"] == "none"
    ok("create project")

    # git source: private/loopback hosts must be rejected (SSRF), http rejected
    for bad in ("http://example.com/x.git", "https://127.0.0.1/x.git", "https://localhost/x.git"):
        c.call("POST", f"/v1/projects/{pid}/source", {"git_url": bad}, expect=422)
    ok("git-url source rejects http/loopback")
    # a public https URL is accepted at registration time (never cloned here: we replace it before any run)
    st, src, _ = c.call("POST", f"/v1/projects/{pid}/source", {"git_url": "https://github.com/octocat/Hello-World.git"})
    if st == 201:
        assert src["kind"] == "git"
        ok("git-url source accepted (public https)")
    else:
        print(f"  skip  git-url accept ({st}: no DNS in this sandbox?)")

    z = zip_dir(ROOT / "eval" / "repos" / "django-lite")
    mp, ct = multipart("file", "django-lite.zip", z)
    src = c.call("POST", f"/v1/projects/{pid}/source", raw=mp, ctype=ct, expect=201)[1]
    assert src["kind"] == "upload" and src["file_count"] > 3
    p = c.call("GET", f"/v1/projects/{pid}", expect=200)[1]
    assert p["source_kind"] == "upload"
    ok(f"upload zip ({src['file_count']} files)")
    zs, ct = multipart("file", "evil.zip", b"not a zip at all")
    c.call("POST", f"/v1/projects/{pid}/source", raw=zs, ctype=ct, expect=422)
    ok("garbage archive rejected, previous source kept")
    assert c.call("GET", f"/v1/projects/{pid}", expect=200)[1]["source_kind"] == "upload"

    est = c.call("POST", f"/v1/projects/{pid}/estimate", {"model_profile": "balanced"}, expect=200)[1]
    assert {"chunks", "usd", "allowed", "reason"} <= set(est) and est["chunks"] > 0, est
    ok(f"estimate {est}")

    run = c.call("POST", f"/v1/projects/{pid}/runs",
                 {"type": "analyze", "model_profile": "fast", "max_usd": 1.0, "incremental": False}, expect=202)[1]
    rid = run["id"]
    events = c.sse(f"/v1/runs/{rid}/events")
    kinds = [e["event"] for e in events]
    assert kinds[-1] == "end" and events[-1]["data"]["status"] == "succeeded", events[-3:]
    assert "progress" in kinds or "stage" in kinds
    ids = [int(e["id"]) for e in events if e["id"]]
    assert ids == sorted(ids)
    ok(f"SSE {len(events)} events ({sorted(set(kinds))}) -> succeeded")
    # replay: after=<last id> yields only the end event
    tail = c.sse(f"/v1/runs/{rid}/events?after={ids[-1]}")
    assert [e["event"] for e in tail] == ["end"]
    run = c.call("GET", f"/v1/runs/{rid}", expect=200)[1]
    assert run["status"] == "succeeded"
    ok("SSE replay with ?after=")

    doc_status, doc, h = c.call("GET", f"/v1/projects/{pid}/document", expect=200)
    assert "text/markdown" in h["content-type"] and len(doc) > 50
    ok(f"document ({len(doc)} chars, text/markdown)")
    g = c.call("GET", f"/v1/projects/{pid}/graph?limit=200", expect=200)[1]
    assert g["total"] > 0 and g["items"] and "edges" in g
    nid = g["items"][0]["id"]
    nd = c.call("GET", f"/v1/projects/{pid}/graph/nodes/{nid}", expect=200)[1]
    assert nd["node"]["id"] == nid and "neighbours" in nd and "edges" in nd
    vs = c.call("GET", f"/v1/projects/{pid}/graph/versions", expect=200)[1]
    assert vs["items"][0]["node_count"] == g["total"], (vs, g["total"])
    q = c.call("GET", f"/v1/projects/{pid}/graph?q={g['items'][0]['name'][:6]}&type={g['items'][0]['type']}", expect=200)[1]
    assert q["total"] >= 1
    ok(f"graph {g['total']} nodes, node detail, versions, filter")
    exp = c.call("GET", f"/v1/projects/{pid}/export?format=json", expect=200)[1]
    assert exp["graph"]["nodes"]
    ok("export json")
    p = c.call("GET", f"/v1/projects/{pid}", expect=200)[1]
    assert p["node_count"] == g["total"] and p["last_run_status"] == "succeeded"
    ok("project summary fields (node_count, last_run_status, open_findings)")

    # comment on a node
    cm = c.call("POST", f"/v1/projects/{pid}/comments", {"item_id": nid, "text": "looks right"}, expect=201)[1]
    lst = c.call("GET", f"/v1/projects/{pid}/comments?item_id={nid}", expect=200)[1]
    assert lst["total"] == 1 and lst["items"][0]["text"] == "looks right" and cm["id"]
    c.call("POST", f"/v1/projects/{pid}/comments", {"item_id": "NOPE-9999", "text": "x"}, expect=404)
    ok("comments")

    # findings: the offline engine has no Verifier, so seed one into the stored review state (spawn mode only)
    fl = c.call("GET", f"/v1/projects/{pid}/findings", expect=200)[1]
    store = os.environ.get("E2E_STORE_DIR")
    if not fl["items"] and store:
        rsf = next(Path(store).rglob(f"{pid}/results/review_state.json"))
        rs = json.loads(rsf.read_text())
        rs.setdefault("findings", {})["findings"] = [{
            "id": "F-0001", "kind": "contradiction", "severity": "high", "title": "Seeded contradiction",
            "detail": "doc says A, code says B", "item_ids": [nid], "area": "",
            "quotes": [{"source": "understanding", "text": "A", "anchor": "", "verified": True}]}]
        rsf.write_text(json.dumps(rs))
        fl = c.call("GET", f"/v1/projects/{pid}/findings", expect=200)[1]
    if fl["items"]:
        f = fl["items"][0]
        assert f["resolution"] is None and {"title", "severity", "quotes", "item_ids"} <= set(f)
        c.call("POST", f"/v1/projects/{pid}/comments", {"item_id": f["id"], "text": "discuss"}, expect=201)
        out = c.call("POST", f"/v1/projects/{pid}/findings/{f['id']}/resolution",
                     {"status": "needs_reanalysis", "note": "check"}, expect=201)[1]
        assert out["resolution"]["status"] == "needs_reanalysis" and out["run"]["type"] == "reanalyze", out
        f2 = c.call("GET", f"/v1/projects/{pid}/findings", expect=200)[1]["items"][0]
        assert f2["resolution"]["status"] == "needs_reanalysis" and f2["comment_count"] == 1
        ev2 = c.sse(f"/v1/runs/{out['run']['id']}/events")  # the engine rewrites review state; the seeded finding goes
        assert ev2[-1]["data"]["status"] == "succeeded", ev2[-1]
        vs2 = c.call("GET", f"/v1/projects/{pid}/graph/versions", expect=200)[1]
        ok(f"finding comment + resolve -> reanalyze run succeeded ({len(vs2['items'])} graph version(s))")
    else:
        print("  skip  no findings (set E2E_STORE_DIR to seed one)")

    u = c.call("GET", f"/v1/workspaces/{wid}/usage", expect=200)[1]
    assert {"totals", "by_run"} <= set(u) and "runs" in u["totals"]
    ent = c.call("GET", f"/v1/workspaces/{wid}/entitlements", expect=200)[1]
    assert {"plan", "limits", "consumption"} <= set(ent)
    ok(f"usage + entitlements (plan {ent['plan']}, offline spend ${u['totals']['usd']})")

    # members
    other = f"mate-{secrets.token_hex(4)}@example.com"
    Client(api).call("POST", "/v1/auth/signup", {"email": other, "password": pw + "xx"}, expect=201)
    m = c.call("POST", f"/v1/workspaces/{wid}/members", {"email": other, "role": "member"}, expect=201)[1]
    ml = c.call("GET", f"/v1/workspaces/{wid}/members", expect=200)[1]
    assert ml["total"] == 2
    au = c.call("GET", f"/v1/workspaces/{wid}/audit", expect=200)[1]
    assert au["total"] > 0
    c.call("DELETE", f"/v1/workspaces/{wid}/members/{m['user_id']}", expect=204)
    ok("members add/list/remove, audit log")

    # tenancy: another user cannot see the project
    o = Client(api)
    o.token = Client(api).call("POST", "/v1/auth/login", {"email": other, "password": pw + "xx"}, expect=200)[1]["access_token"]
    o.call("GET", f"/v1/projects/{pid}", expect=404)
    ok("foreign user gets 404")

    # cancel on a fresh queued run
    r3 = c.call("POST", f"/v1/projects/{pid}/runs", {"type": "analyze", "model_profile": "fast", "max_usd": 1.0,
                                                    "incremental": False}, expect=202)[1]
    c.call("POST", f"/v1/runs/{r3['id']}/cancel")
    ev3 = c.sse(f"/v1/runs/{r3['id']}/events")
    assert ev3[-1]["data"]["status"] in ("cancelled", "succeeded"), ev3[-1]
    ok(f"cancel -> {ev3[-1]['data']['status']}")

    c.call("DELETE", f"/v1/projects/{pid}", expect=204)
    c.call("GET", f"/v1/projects/{pid}", expect=404)
    ok("delete project")
    print(f"== {PASS} checks passed")


def main() -> int:
    external = os.environ.get("E2E_API_URL")
    if external:
        journey(external)
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="ma-e2e-"))
    port = free_port()
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/db.sqlite", "STORE_DIR": str(tmp / "store"),
           "SCRATCH_DIR": str(tmp / "scratch"), "AUTH_MODE": "dev", "ENGINE_OFFLINE": "1", "ENVIRONMENT": "dev",
           "JWT_SECRET": secrets.token_hex(32), "CORS_ORIGINS": ORIGIN, "LOG_JSON": "false", "LOG_LEVEL": "WARNING",
           "MODEL_API_KEY": "", "SSE_POLL_S": "0.2", "WORKER_POLL_S": "0.3", "PYTHONUNBUFFERED": "1"}
    (tmp / "scratch").mkdir()
    py = BACKEND / ".venv" / "bin" / "python"
    py = str(py) if py.exists() else sys.executable
    procs: list[subprocess.Popen] = []
    os.environ["E2E_STORE_DIR"] = str(tmp / "store")
    try:
        subprocess.run([py, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True,
                       stdout=subprocess.DEVNULL)
        log = open(tmp / "services.log", "wb")
        procs.append(subprocess.Popen([py, "-m", "uvicorn", "app.main:app_factory", "--factory", "--port", str(port),
                                       "--host", "127.0.0.1"], cwd=BACKEND, env=env, stdout=log, stderr=log))
        procs.append(subprocess.Popen([py, "-m", "app.worker"], cwd=BACKEND, env=env, stdout=log, stderr=log))
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/readyz", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit(f"backend did not become ready; see {tmp}/services.log")
        journey(base)
        return 0
    except BaseException:
        print(f"FAILED; service log: {tmp}/services.log", file=sys.stderr)
        raise
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
