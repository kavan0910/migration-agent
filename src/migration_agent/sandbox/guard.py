"""In-process, defense-in-depth guard for sandboxed worker processes.

Call :func:`enforce_readonly_process` once at worker start-up.  It is a
tripwire against *accidental or naive* escapes (a tool that tries to write a
file, spawn a process, or phone home).  It is NOT a security boundary against
hostile code running in the same interpreter: audit hooks cannot be removed,
but ctypes/native code can bypass them.  The container is the real boundary;
see README.md in this package.
"""

from __future__ import annotations

import errno
import os
import socket
import sys
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class GuardViolation(PermissionError):
    """Raised (as PermissionError) when the process guard blocks an action."""

    def __init__(self, message: str) -> None:
        super().__init__(errno.EPERM, message)


class SandboxPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Hostnames / IP literals that outbound connections may reach.
    allowed_hosts: list[str] = Field(default_factory=list)
    # Directories (scratch space) where writes/deletes/renames are permitted.
    write_dirs: list[Path] = Field(default_factory=list)
    # Block subprocess / exec / fork.
    block_process_spawn: bool = True


_WRITE_FLAGS = (
    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
    | getattr(os, "O_TMPFILE", 0)
)
_SPAWN_EVENTS = {
    "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty",
}
# event -> indexes of args that are filesystem paths being mutated
_MUTATING_EVENTS: dict[str, tuple[int, ...]] = {
    "os.remove": (0,), "os.rmdir": (0,), "os.mkdir": (0,), "os.rename": (0, 1),
    "os.symlink": (1,), "os.link": (0, 1), "os.truncate": (0,), "os.chmod": (0,),
    "os.chown": (0,), "os.utime": (0,), "os.mkfifo": (0,), "os.mknod": (0,),
    "shutil.copyfile": (1,), "shutil.copymode": (1,), "shutil.copystat": (1,),
    "shutil.copytree": (1,), "shutil.move": (0, 1), "shutil.rmtree": (0,),
    "shutil.make_archive": (0,), "shutil.unpack_archive": (1,),
}
_SOCKET_EVENTS = {"socket.connect", "socket.sendto", "socket.sendmsg"}
_DNS_EVENTS = {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}

_lock = threading.Lock()
_active: "_State | None" = None


class _State:
    def __init__(self, policy: SandboxPolicy) -> None:
        self.policy = policy
        self.write_dirs = [os.path.realpath(p) for p in policy.write_dirs]
        hosts: set[str] = {h.lower() for h in policy.allowed_hosts}
        for h in list(hosts):  # pre-resolve names so connects to IPs are allowed
            try:
                for info in socket.getaddrinfo(h, None):
                    hosts.add(str(info[4][0]).lower())
            except OSError:
                pass
        self.hosts = hosts

    def path_writable(self, p: object) -> bool:
        if isinstance(p, int):
            return True  # fd: was audited when opened
        try:
            real = os.path.realpath(os.fsdecode(p))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        if real == os.devnull:
            return True
        return any(real == d or real.startswith(d + os.sep) for d in self.write_dirs)

    def host_allowed(self, host: object) -> bool:
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if not isinstance(host, str):
            return False
        return host.lower() in self.hosts


def _deny(msg: str) -> None:
    raise GuardViolation(msg)


def _check_address(state: _State, address: object, what: str) -> None:
    if isinstance(address, tuple) and address:
        host = address[0]
    else:
        host = address  # AF_UNIX path etc.
    if not state.host_allowed(host):
        _deny(f"{what} to non-allowlisted destination {host!r} blocked by sandbox")


def _audit(event: str, args: tuple) -> None:
    st = _active
    if st is None:
        return
    if event == "open":
        path, mode, flags = (list(args) + [None, None])[:3]
        writing = False
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            writing = True
        if isinstance(flags, int) and flags & _WRITE_FLAGS:
            writing = True
        if writing and not st.path_writable(path):
            _deny(f"write to {path!r} outside allowed scratch dirs blocked by sandbox")
    elif event in _MUTATING_EVENTS:
        for i in _MUTATING_EVENTS[event]:
            if i < len(args) and not st.path_writable(args[i]):
                _deny(f"{event} on {args[i]!r} outside allowed scratch dirs blocked by sandbox")
    elif event in _SPAWN_EVENTS:
        if st.policy.block_process_spawn:
            _deny(f"{event} blocked by sandbox")
    elif event in _SOCKET_EVENTS:
        if len(args) >= 2:
            _check_address(st, args[1], "outbound socket")
    elif event in _DNS_EVENTS:
        if args and not st.host_allowed(args[0]):
            _deny(f"name resolution of {args[0]!r} blocked by sandbox")


def _patch_sockets() -> None:
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_create = socket.create_connection

    def connect(self, address):  # type: ignore[no-untyped-def]
        if _active is not None:
            _check_address(_active, address, "outbound connect")
        return orig_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        if _active is not None:
            _check_address(_active, address, "outbound connect")
        return orig_connect_ex(self, address)

    def create_connection(address, *a, **kw):  # type: ignore[no-untyped-def]
        if _active is not None:
            _check_address(_active, address, "outbound connect")
        return orig_create(address, *a, **kw)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection


def enforce_readonly_process(policy: SandboxPolicy | None = None) -> SandboxPolicy:
    """Activate the guard for the rest of this process's lifetime (irreversible)."""
    global _active
    policy = policy or SandboxPolicy()
    with _lock:
        if _active is not None:
            raise RuntimeError("sandbox guard is already active and cannot be changed")
        state = _State(policy)
        sys.dont_write_bytecode = True
        _patch_sockets()
        sys.addaudithook(_audit)
        _active = state
    return policy
