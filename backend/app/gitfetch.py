"""Hardened shallow clone of a public https repository (worker side).

Controls: https-only protocol allowlist, SSRF validation + DNS pinning (``http.curloptResolve``), redirects off,
hooks disabled, no submodules, no credentials/prompts, minimal environment, wall-clock timeout, size watch while
cloning, ``.git`` removed and symlinks stripped afterwards. Residual risk: git's libcurl still resolves the host
itself; pinning makes it reuse the validated address. Production should also route worker egress through an
allowlisting proxy (deploy/).
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from .config import Settings
from .safety import UnsafeInput, dir_size, strip_unsafe_tree, validate_git_url


def _git_env(home: Path) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "https",
            "GIT_ASKPASS": "/bin/false", "LC_ALL": "C"}


def build_clone_args(settings: Settings, url: str, dest: Path, host: str, addrs: tuple[str, ...]) -> list[str]:
    pin = f"{host}:443:{','.join(addrs)}"
    return [settings.git_binary,
            "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
            "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
            "-c", f"http.curloptResolve={pin}", "-c", "submodule.recurse=false",
            "clone", "--depth", "1", "--single-branch", "--no-tags", "--no-recurse-submodules", "--", url, str(dest)]


def clone_public_repo(url: str, dest: Path, settings: Settings,
                      runner: Callable[..., subprocess.Popen] = subprocess.Popen,
                      resolver=None) -> tuple[int, int]:
    """Clone into ``dest`` (must not exist). Returns (bytes, files). Raises UnsafeInput on policy violations."""
    kw = {"resolver": resolver} if resolver else {}
    safe = validate_git_url(url, allowed_hosts=settings.allowed_git_hosts, **kw)
    limit = settings.git_max_mb * 1024 * 1024
    home = Path(tempfile.mkdtemp(prefix="git-home-"))
    try:
        args = build_clone_args(settings, safe.url, dest, safe.host, safe.addresses)
        proc = runner(args, env=_git_env(home), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + settings.git_timeout_s
        while True:
            try:
                rc = proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() > deadline:
                _kill(proc)
                raise UnsafeInput("git clone timed out")
            if dest.exists() and dir_size(dest)[0] > limit:
                _kill(proc)
                raise UnsafeInput("repository exceeds the size limit")
        if rc != 0:
            raise UnsafeInput("git clone failed (repository not found or not public)")
        shutil.rmtree(dest / ".git", ignore_errors=True)
        strip_unsafe_tree(dest)
        size, files = dir_size(dest)
        if size > limit:
            raise UnsafeInput("repository exceeds the size limit")
        if files == 0:
            raise UnsafeInput("repository is empty")
        return size, files
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _kill(proc) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
