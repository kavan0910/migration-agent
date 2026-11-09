"""D-06(b): git source hardening, using only a local bare repo (no network)."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from migration_agent import ingest as ing
from migration_agent.ingest import IngestError, ingest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def test_hooks_do_not_run_and_submodules_lfs_not_fetched(tmp_path, mk, monkeypatch):
    marker = tmp_path / "PWNED"
    bare = mk.make_git_repo(tmp_path / "g", marker)
    # hostile *user* git config: template dir that installs a hook into every clone + url rewriting
    home = tmp_path / "home"
    tpl = home / "tpl" / "hooks"
    tpl.mkdir(parents=True)
    hook = tpl / "post-checkout"
    hook.write_text(f"#!/bin/sh\necho pwned > '{marker}'\n")
    hook.chmod(0o755)
    (home / ".gitconfig").write_text(f"[init]\n\ttemplateDir = {home}/tpl\n[core]\n\thooksPath = {home}/tpl/hooks\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))

    dest = tmp_path / "code"
    rep = ingest(f"file://{bare}", dest)
    assert not marker.exists(), "a git hook executed during ingest"
    assert rep.source_type == "git" and rep.commit
    assert (dest / "app.py").exists()
    assert not (dest / ".git").exists() and not (dest / "evil").exists()
    assert not (dest / ".gitmodules").exists() or "evil" not in [p.name for p in dest.iterdir() if p.is_dir()]


def test_clone_command_is_hardened(tmp_path, mk, monkeypatch):
    seen = {}
    real = subprocess.run

    def spy(cmd, *a, **kw):
        if "clone" in cmd:
            seen["cmd"], seen["env"] = list(cmd), dict(kw.get("env") or {})
        return real(cmd, *a, **kw)

    monkeypatch.setattr(ing.subprocess, "run", spy)
    bare = mk.make_git_repo(tmp_path / "g", tmp_path / "PWNED")
    ingest(f"file://{bare}", tmp_path / "code")
    cmd, env = seen["cmd"], seen["env"]
    assert "core.hooksPath=/dev/null" in cmd
    assert "--no-recurse-submodules" in cmd and "--depth" in cmd
    assert "--" in cmd and cmd.index("--") < cmd.index(f"file://{bare}")  # URL can't be parsed as an option
    assert "filter.lfs.smudge=" in cmd and env["GIT_LFS_SKIP_SMUDGE"] == "1"
    assert env["GIT_ALLOW_PROTOCOL"] == "https:ssh:file" and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.mark.parametrize("url", [
    "ext::sh -c 'touch {m}'", "git://example.invalid/x.git", "http://example.invalid/x.git", "ftp://example.invalid/x.git",
])
def test_dangerous_transports_rejected(tmp_path, url):
    marker = tmp_path / "PWNED"
    with pytest.raises(IngestError, match="not allowed"):
        ingest(url.format(m=marker), tmp_path / "code")
    assert not marker.exists()


def test_option_injection_in_ssh_host_does_not_execute(tmp_path):
    marker = tmp_path / "PWNED"
    with pytest.raises(IngestError):
        ingest(f"ssh://-oProxyCommand=touch%20{marker}/repo", tmp_path / "code")
    assert not marker.exists()
