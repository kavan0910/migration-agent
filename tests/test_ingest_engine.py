import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from migration_agent.ingest import IngestError, ingest

AWS = "AKIA" + "IOSFODNN7EXAMPLE"
PW = "hunter2hunter2"


def make_tree(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    (root / "README.md").write_text("# demo\n")
    return root


def test_plain_dir(tmp_path):
    src = make_tree(tmp_path / "src_dir")
    r = ingest(str(src), tmp_path / "code")
    assert r.source_type == "directory"
    assert r.files_copied == 2
    assert (tmp_path / "code/src/app.py").read_text() == "print('hi')\n"
    assert len(r.source_hash) == 64
    r2 = ingest(str(src), tmp_path / "code2")
    assert r2.source_hash == r.source_hash


def test_nonempty_dest_and_missing_source(tmp_path):
    src = make_tree(tmp_path / "s")
    (tmp_path / "c").mkdir()
    (tmp_path / "c/x").write_text("x")
    with pytest.raises(IngestError, match="not empty"):
        ingest(str(src), tmp_path / "c")
    with pytest.raises(IngestError, match="not found"):
        ingest(str(tmp_path / "nope"), tmp_path / "d")


def test_exclusions(tmp_path):
    src = make_tree(tmp_path / "s")
    for d in (".git", "node_modules/x", "vendor", "build", "dist", "target", "src/__pycache__"):
        (src / d).mkdir(parents=True, exist_ok=True)
        (src / d / "f.js").write_text("x")
    (src / "logo.png").write_bytes(b"\x89PNG")
    (src / "blob.weird").write_bytes(b"ab\x00cd")
    (src / "big.txt").write_text("a" * (1024 * 1024 + 1))
    (src / "link").symlink_to(src / "README.md")
    r = ingest(str(src), tmp_path / "code")
    ex = {e.path: e.reason for e in r.exclusions}
    assert ex[".git/"] == "vcs"
    assert ex["node_modules/"] == "dependencies"
    assert ex["vendor/"] == "dependencies"
    assert ex["build/"] == ex["dist/"] == ex["target/"] == "build_output"
    assert ex["src/__pycache__/"] == "cache"
    assert ex["logo.png"] == "binary"
    assert ex["blob.weird"] == "binary"
    assert ex["big.txt"] == "too_large"
    assert ex["link"] == "symlink"
    assert not (tmp_path / "code/node_modules").exists()
    assert r.files_copied == 2


def test_secret_scan(tmp_path):
    src = make_tree(tmp_path / "s")
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJB\n-----END RSA PRIVATE KEY-----\n"
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEF123_-xyz"
    (src / "cfg.env").write_text(f"aws={AWS}\napi_key = 'sk_live_abcdef123456'\npassword={PW}\nt={jwt}\n")
    (src / "id.pem").write_text(pem)
    (src / "ok.py").write_text("password = os.environ['X']\nx = '${PASSWORD}'\n")
    r = ingest(str(src), tmp_path / "code")
    kinds = {(f.file, f.kind) for f in r.secret_findings}
    assert ("cfg.env", "aws_access_key") in kinds
    assert ("cfg.env", "generic_secret") in kinds
    assert ("cfg.env", "jwt") in kinds
    assert ("id.pem", "private_key") in kinds
    assert sorted(r.files_with_secrets) == ["cfg.env", "id.pem"]
    assert {f.line for f in r.secret_findings if f.kind == "aws_access_key"} == {1}
    dumped = r.model_dump_json()
    for secret in (AWS, PW, "sk_live_abcdef123456", jwt, "MIIBOgIBAAJB"):
        assert secret not in dumped
    assert "[REDACTED:aws_access_key]" in dumped


def test_zip_ok_and_hash(tmp_path):
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("proj/a.py", "x=1\n")
        zf.writestr("proj/node_modules/m.js", "x")
    r = ingest(str(z), tmp_path / "code")
    assert r.source_type == "zip"
    assert (tmp_path / "code/proj/a.py").exists()
    assert not (tmp_path / "code/proj/node_modules").exists()
    import hashlib
    assert r.source_hash == hashlib.sha256(z.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", ["../evil.txt", "/abs/evil.txt", "a/../../evil.txt"])
def test_zip_traversal(tmp_path, name):
    z = tmp_path / "t.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(name, "pwn")
    with pytest.raises(IngestError, match="unsafe archive entry"):
        ingest(str(z), tmp_path / "code")
    assert not (tmp_path / "evil.txt").exists()


def test_zip_symlink_escape(tmp_path):
    z = tmp_path / "s.zip"
    with zipfile.ZipFile(z, "w") as zf:
        i = zipfile.ZipInfo("link")
        i.external_attr = (0o120777 << 16)
        zf.writestr(i, "../../etc/passwd")
    with pytest.raises(IngestError, match="symlink"):
        ingest(str(z), tmp_path / "code")


def test_zip_bomb_ratio(tmp_path):
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.txt", b"\0" * (20 * 1024 * 1024))
    with pytest.raises(IngestError, match="ratio"):
        ingest(str(z), tmp_path / "code")


def test_zip_total_size_and_file_count(tmp_path):
    z = tmp_path / "z.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for i in range(5):
            zf.writestr(f"f{i}.txt", "x" * 100)
    with pytest.raises(IngestError, match="too many files"):
        ingest(str(z), tmp_path / "c1", max_files=3)
    with pytest.raises(IngestError, match="size"):
        ingest(str(z), tmp_path / "c2", max_bytes=200)


def _tar(path: Path, mode: str, add):
    with tarfile.open(path, mode) as tf:
        add(tf)


def _addfile(tf, name, data=b"x"):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))


@pytest.mark.parametrize("fname,mode", [("a.tar", "w"), ("a.tar.gz", "w:gz"), ("a.tgz", "w:gz")])
def test_tar_ok(tmp_path, fname, mode):
    t = tmp_path / fname
    _tar(t, mode, lambda tf: _addfile(tf, "p/a.py", b"x=1\n"))
    r = ingest(str(t), tmp_path / "code")
    assert r.source_type == "tar"
    assert (tmp_path / "code/p/a.py").exists()


def test_tar_traversal(tmp_path):
    t = tmp_path / "t.tar"
    _tar(t, "w", lambda tf: _addfile(tf, "../evil", b"x"))
    with pytest.raises(IngestError, match="traversal"):
        ingest(str(t), tmp_path / "code")


def test_tar_symlink_escape(tmp_path):
    t = tmp_path / "s.tar.gz"

    def add(tf):
        ti = tarfile.TarInfo("link")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "../../etc/passwd"
        tf.addfile(ti)

    _tar(t, "w:gz", add)
    with pytest.raises(IngestError, match="symlink"):
        ingest(str(t), tmp_path / "code")


def test_tar_absolute_symlink_and_internal_symlink(tmp_path):
    t = tmp_path / "s.tar"

    def add(tf):
        ti = tarfile.TarInfo("abs")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        tf.addfile(ti)

    _tar(t, "w", add)
    with pytest.raises(IngestError, match="symlink"):
        ingest(str(t), tmp_path / "code")

    t2 = tmp_path / "ok.tar"

    def add2(tf):
        _addfile(tf, "a/real.py")
        ti = tarfile.TarInfo("a/link.py")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "real.py"
        tf.addfile(ti)

    _tar(t2, "w", add2)
    ingest(str(t2), tmp_path / "code2")
    assert not (tmp_path / "code2/a/link.py").exists()


def test_tar_bomb(tmp_path):
    t = tmp_path / "b.tar.gz"
    _tar(t, "w:gz", lambda tf: _addfile(tf, "z", b"\0" * (30 * 1024 * 1024)))
    with pytest.raises(IngestError, match="ratio"):
        ingest(str(t), tmp_path / "code")


def test_unsupported(tmp_path):
    f = tmp_path / "x.rar"
    f.write_text("x")
    with pytest.raises(IngestError, match="unsupported"):
        ingest(str(f), tmp_path / "code")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


def test_git_clone_file_url_no_hooks(tmp_path):
    repo = make_tree(tmp_path / "repo")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "k.env").write_text(f"k={AWS}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    r = ingest(f"file://{repo}", tmp_path / "code")
    assert r.source_type == "git"
    assert r.commit and len(r.commit) == 40
    assert (tmp_path / "code/src/app.py").exists()
    assert not (tmp_path / "code/.git").exists()
    assert r.files_with_secrets == ["k.env"]


def test_git_protocol_allowlist_and_failure(tmp_path):
    with pytest.raises(IngestError, match="protocol"):
        ingest("http://example.com/x.git", tmp_path / "c1")
    with pytest.raises(IngestError, match="protocol"):
        ingest("ext::sh -c touch% /tmp/pwned", tmp_path / "c2")
    with pytest.raises(IngestError, match="git clone failed"):
        ingest(f"file://{tmp_path}/missing", tmp_path / "c3")
