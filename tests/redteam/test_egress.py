"""D-06(f): the offline pipeline opens no outbound connections, and model traffic only leaves via the
configured transport. Network primitives are instrumented; any non-loopback attempt fails the test."""
from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path

import pytest
from click.testing import CliRunner

from migration_agent.cli import main
from migration_agent.llm.litellm_client import LLMConfig, LiteLLMClient

from rt_helpers import RecordingTransport

REPO = Path(__file__).resolve().parents[2] / "eval" / "repos" / "express-crud"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class NetSpy:
    def __init__(self):
        self.attempts: list[tuple[str, object]] = []
        self.spawned: list[object] = []

    def external(self):
        return [a for a in self.attempts if not _is_loopback(a[1])]


def _host_of(addr):
    return addr[0] if isinstance(addr, tuple) and addr else addr


def _is_loopback(addr) -> bool:
    return str(_host_of(addr)) in LOOPBACK


@pytest.fixture
def spy(monkeypatch):
    s = NetSpy()
    real_connect, real_ex, real_cc = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    real_gai = socket.getaddrinfo

    def connect(self, addr):
        s.attempts.append(("connect", addr))
        if not _is_loopback(addr) and self.family != socket.AF_UNIX:
            raise AssertionError(f"outbound connect to {addr!r}")
        return real_connect(self, addr)

    def connect_ex(self, addr):
        s.attempts.append(("connect_ex", addr))
        if not _is_loopback(addr) and self.family != socket.AF_UNIX:
            raise AssertionError(f"outbound connect_ex to {addr!r}")
        return real_ex(self, addr)

    def create_connection(addr, *a, **kw):
        s.attempts.append(("create_connection", addr))
        if not _is_loopback(addr):
            raise AssertionError(f"outbound create_connection to {addr!r}")
        return real_cc(addr, *a, **kw)

    def gai(host, *a, **kw):
        if host not in LOOPBACK and host not in (None, "") and not str(host).startswith("127."):
            s.attempts.append(("getaddrinfo", host))
            raise AssertionError(f"DNS lookup of {host!r}")
        return real_gai(host, *a, **kw)

    def popen(*a, **kw):
        s.spawned.append(a[0] if a else kw.get("args"))
        raise AssertionError(f"subprocess spawned: {a[0] if a else kw.get('args')!r}")

    def urlopen(*a, **kw):
        s.attempts.append(("urlopen", a[0] if a else None))
        raise AssertionError("urllib.request.urlopen used")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", gai)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    # the offline path must never even construct the LLM transport
    import migration_agent.llm.litellm_client as lc
    monkeypatch.setattr(lc, "_default_transport", lambda: (_ for _ in ()).throw(AssertionError("LiteLLM transport created offline")))
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return s


def test_offline_pipeline_opens_no_connections_and_spawns_nothing(spy, tmp_path):
    proj = tmp_path / "p.migration"
    r = CliRunner()
    for args in (["init", str(REPO), "--project", str(proj)], ["ingest", str(proj)], ["extract", str(proj)],
                 ["render", str(proj)], ["analyze", str(proj), "--offline"], ["audit", str(proj)],
                 ["changelog", str(proj)]):
        res = r.invoke(main, args)
        assert res.exit_code == 0, (args, res.output, res.exception)
    assert spy.external() == [] and spy.attempts == []
    assert spy.spawned == []


def test_offline_pipeline_on_hostile_sources_still_no_network(spy, tmp_path, mk):
    repo = tmp_path / "repo"
    mk.make_injection_repo(repo)
    mk.make_secret_repo(tmp_path / "repo2")
    for name, src in (("a", repo), ("b", tmp_path / "repo2")):
        proj = tmp_path / f"{name}.migration"
        r = CliRunner()
        for args in (["init", str(src), "--project", str(proj)], ["ingest", str(proj)], ["extract", str(proj)],
                     ["analyze", str(proj), "--offline"]):
            res = r.invoke(main, args)
            assert res.exit_code == 0, (args, res.output)
    assert spy.attempts == [] and spy.spawned == []


def test_archive_ingest_opens_no_connections(spy, tmp_path, mk):
    z = mk.make_nested_zip(tmp_path / "n.zip")
    t = mk.make_tar(tmp_path / "t.tar.gz", [dict(name="a.py", data=b"x=1\n")])
    from migration_agent.ingest import ingest
    ingest(str(z), tmp_path / "c1")
    ingest(str(t), tmp_path / "c2")
    assert spy.attempts == [] and spy.spawned == []


def test_review_server_only_talks_to_loopback(spy, tmp_path):
    from migration_agent.project import Project
    from migration_agent.review_ui.server import serve
    p = Project.init(tmp_path / "p", source="s")
    server, url = serve(p.root)
    try:
        assert server.server_address[0] == "127.0.0.1"
        c = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        c.request("GET", "/" + url.rstrip("/").rsplit("/", 1)[-1] + "/api/state", headers={"Host": f"127.0.0.1:{server.port}"})
        assert c.getresponse().status == 200
        c.close()
    finally:
        server.stop()
    assert spy.external() == []
    assert all(a[0] in ("connect", "connect_ex", "create_connection") and _is_loopback(a[1]) for a in spy.attempts)


def test_git_clone_is_the_only_ingest_network_path_and_is_gated(tmp_path, monkeypatch):
    """Only a git URL can make ingest touch the network; local sources never invoke git."""
    from migration_agent import ingest as ing
    called = []
    monkeypatch.setattr(ing.subprocess, "run", lambda *a, **k: called.append(a[0]) or (_ for _ in ()).throw(AssertionError))
    d = tmp_path / "src"
    d.mkdir()
    (d / "a.py").write_text("x=1\n")
    ing.ingest(str(d), tmp_path / "code")
    assert called == []


# ---- model traffic ----------------------------------------------------------------
def test_llm_requests_go_only_through_the_injected_transport(tmp_path):
    t = RecordingTransport(reply=lambda s, u: '{"nodes": []}')
    c = LiteLLMClient(LLMConfig(), transport=t, sleep=lambda s: None)
    c.complete(task="chunk_extract", system="s", prompt="p")
    assert len(t.requests) == 1
    kw = t.requests[0]
    allowed = {"model", "messages", "max_tokens", "timeout", "response_format", "api_base", "api_key", "base_url"}
    assert set(kw) <= allowed
    # no per-request destination override unless explicitly configured
    for k in ("api_base", "base_url"):
        assert k not in kw or kw[k] == getattr(c.config, k, None)


def test_default_transport_is_litellm_completion_only():
    litellm = pytest.importorskip("litellm")
    from migration_agent.llm.litellm_client import _default_transport
    assert _default_transport() is litellm.completion


def test_cached_responses_make_no_second_request(tmp_path):
    t = RecordingTransport(reply=lambda s, u: '{"nodes": []}')
    c = LiteLLMClient(LLMConfig(), cache_dir=tmp_path / "c", transport=t, sleep=lambda s: None)
    c.complete(task="chunk_extract", system="s", prompt="p")
    c.complete(task="chunk_extract", system="s", prompt="p")
    assert len(t.requests) == 1


# ---- process guard (runs in a subprocess so audit hooks don't leak into pytest) ---
def test_process_guard_blocks_non_allowlisted_destinations(tmp_path):
    code = textwrap.dedent("""
        import socket, sys
        from migration_agent.sandbox.guard import enforce_readonly_process, SandboxPolicy
        enforce_readonly_process(SandboxPolicy(allowed_hosts=["127.0.0.1"], write_dirs=[]))
        res = {}
        for name, host in (("blocked", "203.0.113.7"), ("allowed", "127.0.0.1")):
            try:
                socket.create_connection((host, 9), timeout=1)
                res[name] = "connected"
            except PermissionError:
                res[name] = "guard"
            except OSError:
                res[name] = "os"          # allowed by guard; port closed
        try:
            socket.getaddrinfo("attacker.example", 443)
            res["dns"] = "resolved"
        except PermissionError:
            res["dns"] = "guard"
        except OSError:
            res["dns"] = "os"
        print(res)
    """)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=30)
    assert out.returncode == 0, out.stderr
    assert "'blocked': 'guard'" in out.stdout and "'dns': 'guard'" in out.stdout
    assert "'allowed': 'guard'" not in out.stdout


def test_squid_allowlist_documents_single_model_host():
    conf = (Path(__file__).resolve().parents[2] / "docker" / "squid.conf").read_text()
    acls = [ln for ln in conf.splitlines() if ln.startswith("acl model_host")]
    assert len(acls) == 1 and conf.count("http_access allow") == 1 and "http_access deny all" in conf
