"""Guard tests run in subprocesses so audit hooks never leak into pytest."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from migration_agent.sandbox.guard import SandboxPolicy


SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(body: str, tmp_path, **policy):
    prelude = textwrap.dedent(f"""
        import json, sys
        from pathlib import Path
        from migration_agent.sandbox.guard import enforce_readonly_process, SandboxPolicy
        scratch = Path({str(tmp_path / 'scratch')!r}); scratch.mkdir(exist_ok=True)
        pol = SandboxPolicy(write_dirs=[scratch], **{policy!r})
        enforce_readonly_process(pol)
        out = {{}}
        def attempt(name, fn):
            try:
                fn(); out[name] = "ok"
            except PermissionError as e:
                out[name] = "blocked"
            except Exception as e:
                out[name] = "other:" + type(e).__name__
    """)
    code = prelude + textwrap.dedent(body) + "\nprint('RESULT' + json.dumps(out))\n"
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": SRC})
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.split("RESULT")[-1])


def test_policy_model():
    assert SandboxPolicy().allowed_hosts == []


def test_write_blocked_scratch_allowed(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    r = run(f"""
        v = Path({str(victim)!r})
        attempt("write_outside", lambda: v.write_text("x"))
        attempt("append_outside", lambda: open(v, "a"))
        attempt("create_outside", lambda: open(v.with_name("new.txt"), "w"))
        attempt("remove", lambda: v.unlink())
        attempt("rename", lambda: v.rename(v.with_name("z")))
        attempt("rename_into_scratch", lambda: v.rename(scratch / "z"))
        attempt("read_outside", lambda: v.read_text())
        attempt("write_scratch", lambda: (scratch / "f").write_text("ok"))
        attempt("remove_scratch", lambda: (scratch / "f").unlink())
        attempt("popen", lambda: __import__("subprocess").Popen(["true"]))
        attempt("system", lambda: __import__("os").system("true"))
    """, tmp_path)
    assert r["write_outside"] == r["append_outside"] == r["create_outside"] == "blocked"
    assert r["remove"] == r["rename"] == r["rename_into_scratch"] == "blocked"
    assert r["popen"] == r["system"] == "blocked"
    assert r["read_outside"] == "ok" and r["write_scratch"] == "ok" and r["remove_scratch"] == "ok"
    assert victim.read_text() == "keep"
    assert not (tmp_path / "new.txt").exists()


def test_network_allowlist(tmp_path):
    import socket
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(5)
    port = srv.getsockname()[1]
    try:
        r = run(f"""
            import socket
            attempt("allowed", lambda: socket.create_connection(("127.0.0.1", {port}), timeout=3).close())
            def raw():
                s = socket.socket(); s.settimeout(3); s.connect(("127.0.0.1", {port})); s.close()
            attempt("allowed_raw", raw)
            attempt("denied_ip", lambda: socket.create_connection(("93.184.216.34", 80), timeout=2))
            def raw_bad():
                s = socket.socket(); s.settimeout(2); s.connect(("93.184.216.34", 80))
            attempt("denied_raw", raw_bad)
            attempt("denied_ex", lambda: socket.socket().connect_ex(("10.0.0.1", 80)))
            attempt("denied_dns", lambda: socket.getaddrinfo("example.com", 80))
            def udp():
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b"x", ("8.8.8.8", 53))
            attempt("denied_udp", udp)
        """, tmp_path, allowed_hosts=["127.0.0.1"])
    finally:
        srv.close()
    assert r["allowed"] == "ok" and r["allowed_raw"] == "ok"
    for k in ("denied_ip", "denied_raw", "denied_ex", "denied_dns", "denied_udp"):
        assert r[k] == "blocked", (k, r)


def test_cannot_reactivate(tmp_path):
    r = run("""
        attempt("again", lambda: enforce_readonly_process(SandboxPolicy()))
    """, tmp_path)
    assert r["again"] == "other:RuntimeError"
