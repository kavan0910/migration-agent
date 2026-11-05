# Sandbox baseline (A-09)

Two layers. Be clear about which one you are relying on.

## Layer 1: container (the real boundary) - `docker/`

| Control | Mechanism |
|---|---|
| Code is read-only | repo mounted `:ro` at `/workspace`; image code is root-owned; `--read-only` root fs |
| Scratch only | `tmpfs` on `/tmp` and `/scratch` (noexec, size-limited) |
| No network by default | `docker/run.sh` uses `--network none` (compose profile `offline`: `network_mode: none`) |
| Model-endpoint-only egress | compose profile `model`: worker sits on an `internal: true` network (no route out). The only other member is an allowlisting Squid proxy (`docker/squid.conf`) that permits HTTPS `CONNECT` to the single model host and denies everything else. Worker gets `HTTPS_PROXY`. |
| Privileges | `cap_drop: ALL`, `no-new-privileges`, non-root uid 10001, pids/memory limits |
| Credentials | none in the image; `ANTHROPIC_API_KEY` is passed at runtime and only in `model` mode |

Caveats: the proxy allowlist is by domain name (SNI/CONNECT host), so DNS or the
model host itself could be abused as a channel; pin to the narrowest host. The
proxy container itself needs `SETUID/SETGID` and is a trusted component.
`network_mode: none` and `networks:` are mutually exclusive, hence two services.
This was not exercised in CI: Docker is not available on the dev machine used
to author it, so treat the Docker files as reviewed-but-untested.

## Layer 2: in-process (defense in depth) - `guard.py`, `tools.py`

`ReadOnlyTools(root)` (tools.py): the agent-facing tool surface. Methods:
`read_file`, `list_dir`, `grep`, `file_stat`. Paths are resolved with symlinks
followed and must remain under `root`; `..`, NUL bytes, outside absolute paths
and escaping symlinks raise `SandboxViolation`. Size/line/match caps, binary
files refused. There are no write/exec/network methods. This is enforced by
construction (the capability doesn't exist), not by a filter.

`enforce_readonly_process(SandboxPolicy(allowed_hosts, write_dirs))` (guard.py):
call once at worker start. Irreversible for the process lifetime.
- Sockets: `socket.connect/connect_ex/create_connection` patched, plus audit events
  `socket.connect/sendto/sendmsg/getaddrinfo/gethostbyname` -> only allowlisted hosts
  (names are pre-resolved at activation). AF_UNIX is blocked.
- Writes: audit `open` with write flags/modes outside `write_dirs`; `os.remove/rename/
  mkdir/rmdir/symlink/link/truncate/chmod/chown/utime` and `shutil.*` mutators.
- Process spawn: `subprocess.Popen`, `os.system`, `os.exec*`, `os.spawn*`,
  `os.posix_spawn`, `os.fork`.
- Violations raise `GuardViolation` (a `PermissionError`).

### What the in-process guard does NOT stop
- Code in the same interpreter can use `ctypes`/cffi/native extensions or raw
  syscalls to bypass Python-level hooks (audit hooks can't be removed, but they only see
  what CPython reports). A malicious in-process payload is out of scope.
- Preexisting open file descriptors/sockets are not re-checked.
- Writes through already-open writable fds, `mmap`, or paths via `/proc/self/fd` tricks.
- Allowlist is by host, not port/protocol; DNS names are matched as strings/pre-resolved IPs.
- Reads are not restricted by the guard (only `ReadOnlyTools` jails reads).

Therefore: treat the guard as a tripwire that catches bugs and naive misuse of
tools; rely on the container for isolation from untrusted repositories.

## Tests
`tests/test_sandbox_tools.py`, `tests/test_sandbox_guard.py` (guard tests run in
subprocesses so audit hooks don't leak into pytest). No Docker is required.
