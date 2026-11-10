import pytest

from app.config import Settings
from app.safety import UnsafeInput, validate_git_url

PUB = lambda h, p: ["140.82.112.3"]  # noqa: E731


@pytest.mark.parametrize("url", [
    "http://github.com/a/b", "ftp://github.com/a/b", "git://github.com/a/b.git", "ssh://git@github.com/a/b",
    "file:///etc/passwd", "ext::sh -c id", "javascript:alert(1)", "//github.com/a/b", "github.com/a/b", "", "https://",
    "https://user:pw@github.com/a/b", "https://user@github.com/a/b", "https://github.com:8443/a/b",
    "https://github.com:22/a/b", "https://localhost/a", "https://LOCALHOST./a", "https://foo.localhost/a",
    "https://127.0.0.1/a", "https://127.1/a", "https://2130706433/a", "https://0x7f.0.0.1/a", "https://0177.0.0.1/a",
    "https://[::1]/a", "https://[::ffff:127.0.0.1]/a", "https://[::ffff:10.0.0.1]/a", "https://10.1.2.3/a",
    "https://172.16.0.9/a", "https://192.168.1.1/a", "https://169.254.169.254/latest/meta-data",
    "https://[fd00:ec2::254]/", "https://metadata.google.internal/", "https://100.64.0.1/a", "https://0.0.0.0/a",
    "https://[fe80::1]/a", "https://[fc00::1]/a", "https://224.0.0.1/a", "https://svc.internal/a", "https://printer.local/a",
    "https://github.com/a/b#frag", "https://github.com/a b", "https://github.com/a\\b", "https://-evil.com/a",
    "https://github.com/a\nHost: evil", "https://" + "a" * 3000 + ".com",
])
def test_blocked_urls(url):
    with pytest.raises(UnsafeInput):
        validate_git_url(url, resolver=PUB)


def test_allows_public_https():
    s = validate_git_url("https://github.com/org/repo.git", resolver=PUB)
    assert s.host == "github.com" and s.port == 443 and s.addresses == ("140.82.112.3",)
    assert validate_git_url("https://github.com:443/org/repo", resolver=PUB).port == 443
    assert validate_git_url("https://8.8.8.8/x", resolver=PUB).addresses == ("8.8.8.8",)


@pytest.mark.parametrize("ips", [["127.0.0.1"], ["10.0.0.5"], ["169.254.169.254"], ["::1"], ["fd00::1"],
                                 ["140.82.112.3", "10.0.0.1"], ["::ffff:192.168.0.1"], ["100.100.100.200"]])
def test_dns_resolving_to_private_is_blocked(ips):
    with pytest.raises(UnsafeInput):
        validate_git_url("https://innocent.example.com/a/b", resolver=lambda h, p: ips)


def test_dns_failure_and_empty():
    def boom(h, p):
        raise UnsafeInput("host could not be resolved")
    with pytest.raises(UnsafeInput):
        validate_git_url("https://nonexistent.example.com/a", resolver=boom)
    with pytest.raises(UnsafeInput):
        validate_git_url("https://empty.example.com/a", resolver=lambda h, p: [])


def test_allowlist():
    with pytest.raises(UnsafeInput):
        validate_git_url("https://gitlab.com/a/b", allowed_hosts={"github.com"}, resolver=PUB)
    assert validate_git_url("https://github.com/a/b", allowed_hosts={"github.com"}, resolver=PUB)


def test_api_rejects_ssrf_and_stores_valid(client, alice, app):
    pid = alice.project(with_source=False)
    bad = ["http://github.com/a/b", "https://127.0.0.1/a", "https://169.254.169.254/x", "https://user:p@github.com/a"]
    for u in bad:
        r = alice.post(f"/v1/projects/{pid}/source", json={"git_url": u})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_source", u
    app.state.resolver = lambda h, p: ["10.0.0.7"]
    assert alice.post(f"/v1/projects/{pid}/source", json={"git_url": "https://rebind.example.com/a"}).status_code == 422
    app.state.resolver = PUB
    r = alice.post(f"/v1/projects/{pid}/source", json={"git_url": "https://github.com/a/b.git"})
    assert r.status_code == 201 and r.json()["kind"] == "git"
    assert alice.post(f"/v1/projects/{pid}/source", json={"nope": 1}).status_code == 422


# ----------------------------------------------------------------------------- the clone command itself
def test_clone_command_is_locked_down(tmp_path):
    from app.gitfetch import build_clone_args
    a = build_clone_args(Settings(), "https://github.com/a/b", tmp_path / "d", "github.com", ("140.82.112.3",))
    joined = " ".join(a)
    for needle in ("core.hooksPath=/dev/null", "http.followRedirects=false", "protocol.allow=never",
                   "protocol.https.allow=always", "http.curloptResolve=github.com:443:140.82.112.3",
                   "--no-recurse-submodules", "--depth 1"):
        assert needle in joined
    assert a[-3] == "--"  # url can never be parsed as an option


def test_clone_rejects_before_spawning(tmp_path):
    from app.gitfetch import clone_public_repo
    spawned = []
    with pytest.raises(UnsafeInput):
        clone_public_repo("https://127.0.0.1/x", tmp_path / "d", Settings(), runner=lambda *a, **k: spawned.append(1))
    assert not spawned


class _Proc:
    def __init__(self, rc, on_start=None, hang=False):
        self.rc, self.hang, self.pid = rc, hang, 99999999
        self.killed = False
        if on_start:
            on_start()

    def wait(self, timeout=None):
        import subprocess
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("git", timeout)
        return self.rc

    def kill(self):
        self.killed = True


def test_clone_happy_path_strips_git_and_symlinks(tmp_path):
    import os
    from app.gitfetch import clone_public_repo
    dest = tmp_path / "d"

    def runner(args, **kw):
        def mk():
            (dest / ".git").mkdir(parents=True)
            (dest / ".git" / "config").write_text("x")
            (dest / "a.py").write_text("print(1)")
            os.symlink("/etc/passwd", dest / "link")
        env = kw["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0" and "GIT_ALLOW_PROTOCOL" in env and "AWS_SECRET_ACCESS_KEY" not in env
        return _Proc(0, mk)

    size, files = clone_public_repo("https://github.com/a/b", dest, Settings(), runner=runner, resolver=lambda h, p: ["140.82.112.3"])
    assert files == 1 and not (dest / ".git").exists() and not (dest / "link").exists()


def test_clone_failure_timeout_and_size(tmp_path):
    from app.gitfetch import clone_public_repo
    res = lambda h, p: ["140.82.112.3"]  # noqa: E731
    with pytest.raises(UnsafeInput):
        clone_public_repo("https://github.com/a/b", tmp_path / "d1", Settings(), runner=lambda *a, **k: _Proc(128), resolver=res)
    with pytest.raises(UnsafeInput):
        clone_public_repo("https://github.com/a/b", tmp_path / "d2", Settings(git_timeout_s=0), runner=lambda *a, **k: _Proc(0, hang=True), resolver=res)
    big = tmp_path / "d3"

    def mk():
        big.mkdir()
        (big / "blob").write_bytes(b"0" * 2 * 1024 * 1024)
    with pytest.raises(UnsafeInput):
        clone_public_repo("https://github.com/a/b", big, Settings(git_max_mb=1), runner=lambda *a, **k: _Proc(0, mk), resolver=res)
    assert not big.exists()
