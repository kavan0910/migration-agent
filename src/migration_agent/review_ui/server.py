"""Local review server (C-06). stdlib only, bound to 127.0.0.1, token-protected."""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field, ValidationError

from migration_agent.graph.models import Graph
from migration_agent.ingest.secrets import redact_string
from migration_agent.graph.store import GraphStoreError, diff_graphs, load_latest, load_snapshot, parse_graph
from migration_agent.project import Project
from migration_agent.review_ui.markdown import anchors, render_sections
from migration_agent.review_ui.page import render_page
from migration_agent.verify.models import (
    Comment, CommentsFile, FindingsFile, Resolution, ResolutionsFile, ResolutionStatus,
)
from migration_agent.viz.graph_html import render_graph_html

MAX_BODY = 64 * 1024
MAX_TEXT = 5000
_SNAP_RE = re.compile(r"v(\d+)\.json")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class _CommentIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    author: str = Field(default="user", max_length=64)


class _ResolutionIn(BaseModel):
    finding_id: str = Field(min_length=1, max_length=64)
    status: ResolutionStatus
    note: str = Field(default="", max_length=MAX_TEXT)


class ApiError(Exception):
    def __init__(self, code: int, msg: str):
        self.code, self.msg = code, msg


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ReviewState:
    """File-backed state for one project (independent of the review/ package)."""

    def __init__(self, project: Project):
        self.project = project
        self.lock = threading.Lock()
        self.fb = project.root / "feedback"

    # -- readers -------------------------------------------------------
    def _load(self, name: str, model):
        p = self.fb / name
        if not p.exists():
            return model()
        return model.model_validate_json(p.read_text(encoding="utf-8"))

    def findings(self) -> FindingsFile:
        return self._load("findings.json", FindingsFile)

    def resolutions(self) -> ResolutionsFile:
        return self._load("resolutions.json", ResolutionsFile)

    def comments(self) -> CommentsFile:
        return self._load("comments.json", CommentsFile)

    def versions(self) -> list[int]:
        d = self.project.graph_dir / "snapshots"
        if not d.is_dir():
            return []
        return sorted(int(m.group(1)) for p in d.iterdir() if (m := _SNAP_RE.fullmatch(p.name)))

    def graph(self) -> Graph | None:
        try:
            return load_latest(self.project.graph_dir)
        except GraphStoreError:
            return None

    def doc_text(self) -> str:
        p = self.project.docs / "understanding.latest.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def known_item_ids(self) -> set[str]:
        ids = anchors(self.doc_text())
        g = self.graph()
        if g:
            ids |= {n.id for n in g.nodes} | {e.id for e in g.edges}
        return ids

    def coverage(self, doc: str, g: Graph | None) -> dict:
        inv: dict[str, int] = {}
        p = self.project.inventory_path
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, bool):
                        continue
                    if isinstance(v, (int, float)):
                        inv[str(k)] = int(v)
                    elif isinstance(v, (list, dict)):
                        inv[str(k)] = len(v)
        by_type: dict[str, int] = {}
        if g:
            for n in g.nodes:
                by_type[n.type] = by_type.get(n.type, 0) + 1
        doc_rows: list[list[str]] = []
        in_cov = False
        for line in doc.splitlines():
            if line.startswith("## "):
                in_cov = "coverage" in line.lower()
                continue
            m = re.match(r"^\s*-\s+([^:]+):\s*(.*)$", line)
            if in_cov and m:
                doc_rows.append([m.group(1).strip(), m.group(2).strip()])
        return {"inventory": inv, "nodes_by_type": by_type, "doc": doc_rows,
                "node_total": len(g.nodes) if g else 0, "edge_total": len(g.edges) if g else 0}

    def state(self) -> dict:
        errors: list[str] = []

        def safe(fn, default):
            try:
                return fn()
            except (ValidationError, ValueError, OSError) as e:
                errors.append(f"{type(e).__name__}: {str(e)[:200]}")
                return default

        findings = safe(self.findings, FindingsFile())
        resolutions = safe(self.resolutions, ResolutionsFile())
        comments = safe(self.comments, CommentsFile())
        doc = safe(self.doc_text, "")
        g = self.graph()
        return {
            "project": safe(lambda: self.project.meta().name, ""),
            "findings": findings.model_dump(),
            "resolutions": resolutions.model_dump(),
            "comments": comments.model_dump(),
            "versions": self.versions(),
            "sections": render_sections(doc),
            "coverage": self.coverage(doc, g),
            "known_ids": sorted(self.known_item_ids()),
            "errors": errors,
        }

    # -- writers -------------------------------------------------------
    def add_comment(self, data: dict) -> dict:
        inp = _CommentIn.model_validate(data)
        text = redact_string(inp.text.strip())  # pasted credentials never reach disk, prompts or the UI
        if not text:
            raise ApiError(400, "text must not be empty")
        with self.lock:
            if inp.item_id not in self.known_item_ids():
                raise ApiError(404, f"unknown item_id: {inp.item_id[:64]}")
            cf = self.comments()
            nums = [int(c.id.split("-")[-1]) for c in cf.comments if c.id.startswith("CM-") and c.id[3:].isdigit()]
            c = Comment(id=f"CM-{(max(nums) if nums else 0) + 1:04d}", item_id=inp.item_id, text=text,
                        author=redact_string(inp.author.strip()) or "user", created=_now())
            cf.comments.append(c)
            _atomic_write(self.fb / "comments.json", cf.model_dump_json(indent=2) + "\n")
        return c.model_dump()

    def add_resolution(self, data: dict) -> dict:
        inp = _ResolutionIn.model_validate(data)
        with self.lock:
            if inp.finding_id not in {f.id for f in self.findings().findings}:
                raise ApiError(404, f"unknown finding_id: {inp.finding_id[:64]}")
            rf = self.resolutions()
            r = Resolution(finding_id=inp.finding_id, status=inp.status, note=redact_string(inp.note.strip()), created=_now())
            rf.resolutions.append(r)
            _atomic_write(self.fb / "resolutions.json", rf.model_dump_json(indent=2) + "\n")
        return r.model_dump()

    def diff(self, a: int, b: int) -> dict:
        vs = set(self.versions())
        if a not in vs or b not in vs:
            raise ApiError(404, "unknown version")
        ga, gb = load_snapshot(self.project.graph_dir, a), load_snapshot(self.project.graph_dir, b)
        d = diff_graphs(ga, gb)
        names = {n.id: n.name for n in ga.nodes} | {n.id: n.name for n in gb.nodes}
        row = lambda ids: [{"id": i, "name": names.get(i, "")} for i in ids]  # noqa: E731
        return {"a": a, "b": b, "added_nodes": row(d.added_nodes), "removed_nodes": row(d.removed_nodes),
                "changed_nodes": row(d.changed_nodes), "added_edges": len(d.added_edges),
                "removed_edges": len(d.removed_edges), "changed_edges": len(d.changed_edges)}


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, state: ReviewState, token: str):
        super().__init__(addr, handler)
        self.state = state
        self.token = token
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.server_address[1]

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


class Handler(BaseHTTPRequestHandler):
    server: ReviewServer
    server_version = "ReviewUI"
    sys_version = ""

    def log_message(self, fmt, *args):  # quiet
        pass

    # -- plumbing ------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                         "connect-src 'self'; img-src data:; frame-src 'self'; frame-ancestors 'self'; "
                         "base-uri 'none'; form-action 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _err(self, code: int, msg: str):
        self._json(code, {"error": msg})

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}")

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin.lower() not in (
                f"http://127.0.0.1:{self.server.port}", f"http://localhost:{self.server.port}"):
            return False
        sfs = self.headers.get("Sec-Fetch-Site")
        return sfs in (None, "same-origin", "none")

    def _eq(self, cand: str | None) -> bool:
        return bool(cand) and hmac.compare_digest(cand.encode(), self.server.token.encode())

    def _authorize(self, segs: list[str]) -> tuple[bool, list[str]]:
        """Token via first path segment, X-Token header, or cookie. Returns (ok, remaining segs)."""
        if segs and self._eq(segs[0]):
            return True, segs[1:]
        if self._eq(self.headers.get("X-Token")):
            return True, segs
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "review_token" and self._eq(v):
                return True, segs
        return False, segs

    def _dispatch(self, method: str):
        try:
            if not self._host_ok():
                return self._err(403, "bad host")
            parts = urlsplit(self.path)
            if "\\" in parts.path or "\x00" in parts.path or ".." in parts.path.split("/"):
                return self._err(400, "bad path")
            segs = [s for s in parts.path.split("/") if s]
            ok, rest = self._authorize(segs)
            if not ok:
                return self._err(403, "forbidden")
            if method == "POST" and not self._origin_ok():
                return self._err(403, "cross-origin request rejected")
            query = parse_qs(parts.query)
            route = "/" + "/".join(rest)
            st = self.server.state
            if method == "GET":
                return self._get(route, query, st)
            if method == "POST":
                return self._post(route, st)
            return self._err(405, "method not allowed")
        except ApiError as e:
            self._err(e.code, e.msg)
        except ValidationError as e:
            self._err(400, "invalid input: " + "; ".join(
                f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:300])
        except (OSError, ValueError, GraphStoreError) as e:
            self._err(500, f"server error: {type(e).__name__}")

    def _get(self, route: str, query: dict, st: ReviewState):
        if route == "/":
            cookie = f"review_token={self.server.token}; Path=/; HttpOnly; SameSite=Strict"
            base = "/" + self.server.token
            return self._send(200, render_page(base).encode(), "text/html; charset=utf-8", {"Set-Cookie": cookie})
        if route == "/api/state":
            return self._json(200, st.state())
        if route == "/api/diff":
            return self._json(200, st.diff(self._int(query, "a"), self._int(query, "b")))
        if route == "/graph":
            v = self._int(query, "v", None)
            prev = self._int(query, "prev", None)
            if v is None:
                g = st.graph()
                if g is None:
                    raise ApiError(404, "no graph")
            else:
                if v not in st.versions():
                    raise ApiError(404, "unknown version")
                g = load_snapshot(st.project.graph_dir, v)
            p = None
            if prev is not None:
                if prev not in st.versions():
                    raise ApiError(404, "unknown version")
                p = load_snapshot(st.project.graph_dir, prev)
            html_ = render_graph_html(g, previous=p, title=f"Graph v{g.version}")
            return self._send(200, html_.encode(), "text/html; charset=utf-8")
        return self._err(404, "not found")

    @staticmethod
    def _int(query: dict, key: str, default=...):
        vals = query.get(key)
        if not vals:
            if default is ...:
                raise ApiError(400, f"missing {key}")
            return default
        if not re.fullmatch(r"\d{1,9}", vals[0]):
            raise ApiError(400, f"bad {key}")
        return int(vals[0])

    def _post(self, route: str, st: ReviewState):
        if route not in ("/api/comments", "/api/resolutions"):
            return self._err(404, "not found")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return self._err(415, "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._err(411, "Content-Length required")
        if length < 0:
            return self._err(400, "bad length")
        if length > MAX_BODY:
            self.close_connection = True
            return self._err(413, "body too large")
        try:
            data = json.loads(self.rfile.read(length) or b"")
        except ValueError:
            return self._err(400, "invalid JSON")
        if not isinstance(data, dict):
            return self._err(400, "JSON object required")
        if route == "/api/comments":
            return self._json(201, st.add_comment(data))
        return self._json(201, st.add_resolution(data))

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")


def serve(project_dir, host: str = "127.0.0.1", port: int = 0, open_browser: bool = False):
    """Start the review server in a background thread. Returns (server, url).

    Only loopback is allowed. Stop with ``server.stop()``.
    """
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("review server only binds to 127.0.0.1")
    project = Project.load(Path(project_dir))
    token = secrets.token_urlsafe(24)
    assert _TOKEN_RE.match(token)
    server = ReviewServer(("127.0.0.1", port), Handler, ReviewState(project), token)
    t = threading.Thread(target=server.serve_forever, name="review-ui", daemon=True)
    t.start()
    server.thread = t
    url = f"http://127.0.0.1:{server.port}/{token}/"
    if open_browser:
        webbrowser.open(url)
    return server, url
