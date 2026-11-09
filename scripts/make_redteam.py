"""Build adversarial red-team fixtures (D-06) into a caller-supplied directory.

Nothing here is committed in dangerous form: zip bombs, traversal archives and secret-laden repos
are generated at test time. "Secrets" are obviously fake (contain EXAMPLE) but match the real
token patterns, and are assembled at runtime so no live-looking credential sits in the source tree.

Usage: python scripts/make_redteam.py OUTDIR   (or import the make_* functions from tests)
"""
from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAYLOADS = HERE.parent / "eval" / "redteam" / "payloads.json"


def payloads() -> dict:
    return json.loads(PAYLOADS.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ secrets
def fake_secrets() -> dict[str, str]:
    """Pattern-matching but obviously fake credentials, built at runtime."""
    ex = "EXAMPLE"
    b64 = lambda s: __import__("base64").urlsafe_b64encode(s.encode()).decode().rstrip("=")  # noqa: E731
    jwt = ".".join([b64('{"alg":"HS256","typ":"JWT"}'), b64('{"sub":"EXAMPLE-user","iat":1}'), ex + "SIGNATURE" + ex])
    pem_body = "\n".join(ex * 8 for _ in range(4))
    return {
        "aws_access_key": "AKIA" + (ex * 3)[:16],
        "aws_secret": "wJalrXUtnFEMI" + "/K7MDENG/bPxRfiCY" + ex + "KEY",
        "github": "ghp_" + ex * 6,
        "slack": "xoxb-" + "1234567890-" + ex + "-" + ex,
        "stripe": "sk_live_" + ex * 3,
        "gcp_api_key": "AIza" + (ex * 5)[:35],
        "jwt": jwt,
        "db_password": "Hunter2" + ex + "pw",
        "config_password": "S3cr3t" + ex + "Passw0rd",
        "private_key_body": ex * 8,
        "private_key": f"-----BEGIN RSA PRIVATE KEY-----\n{pem_body}\n-----END RSA PRIVATE KEY-----\n",
        "env_secret": "EnvOnly" + ex + "Value99",
        "api_key_env": "sk-ant-" + ex + "-" + ex * 3,
    }


def make_secret_repo(root: Path) -> dict[str, str]:
    """A small Express-like service with credentials everywhere. Returns the secret values."""
    s = fake_secrets()
    w = lambda rel, text: (root / rel).parent.mkdir(parents=True, exist_ok=True) or (root / rel).write_text(text)  # noqa: E731
    w("src/app.js", f"""const express = require('express');
const app = express();
// TODO rotate: token {s['github']}
const AWS_ACCESS_KEY_ID = "{s['aws_access_key']}";
const aws_secret_access_key = "{s['aws_secret']}";
const SLACK = '{s['slack']}';
const stripe_key = "{s['stripe']}";
const DB = "postgres://app:{s['db_password']}@db.internal:5432/app";
const session = "{s['jwt']}";
app.get('/health', (req, res) => res.send('ok'));
module.exports = app;
""")
    w("config/settings.yml", f"""database:
  host: db.internal
  password: {s['config_password']}
google:
  api_key: {s['gcp_api_key']}
""")
    w("docker-compose.yml", f"""services:
  db:
    image: postgres
    environment:
      DATABASE_URL: postgres://app:{s['db_password']}@db:5432/app
""")
    w("service.py", f'''"""Service. {s["github"]}"""
API_TOKEN = "{s["slack"]}"
def handler():
    return 1
''')
    w("gcp-service-account.json", json.dumps({
        "type": "service_account", "project_id": "demo",
        "private_key": s["private_key"], "client_email": "x@demo.iam.gserviceaccount.com"}))
    w("deploy/notes.txt", "key material follows\n" + s["private_key"] + "end\n")
    w(".env", f"SECRET_KEY={s['env_secret']}\nANTHROPIC_API_KEY={s['api_key_env']}\nPLAIN=value\n")
    w("id_rsa", s["private_key"])
    w("certs/server.pem", s["private_key"])
    w("README.md", "# demo service\nSee config/settings.yml\n")
    w("package.json", json.dumps({"name": "demo", "version": "1.0.0", "dependencies": {"express": "^4.0.0"}}))
    return s


# --------------------------------------------------------------- injection
def make_injection_repo(root: Path) -> dict:
    """A repo whose comments/strings/docstrings/filenames try to hijack the analyst."""
    p = payloads()
    inj = p["injections"]
    w = lambda rel, text: (root / rel).parent.mkdir(parents=True, exist_ok=True) or (root / rel).write_text(text, encoding="utf-8")  # noqa: E731
    w("src/orders.py", f'''"""{inj[0]}"""
# {inj[1]}
FORGED = "{p["forged_delimiters"][0]}"
def place_order(x):
    """{inj[2]}"""
    # {p["forged_delimiters"][1]}
    return x
''')
    w("src/legacy.js", f"""// {inj[3]}
/* {p["homoglyph_delimiters"][0]} {inj[0]} */
const rlo = "{p["bidi"]}";
module.exports = function legacy() {{ return 1; }};
""")
    w("src/sys_prompt.py", f'def f():\n    return "{inj[4]}"\n')
    # the file NAME itself is a payload (no newlines: ingest would withhold those)
    w(p["injection_filename"], "x = 1\n")
    return p


# ---------------------------------------------------------------- archives
def make_zip_bomb(path: Path, mib: int = 24) -> Path:
    """~25 KB zip that expands to `mib` MiB of zeros (ratio ~1000:1)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("bomb.txt", b"\0" * (mib * 1024 * 1024))
    return path


def make_many_small_files_zip(path: Path, count: int) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for i in range(count):
            z.writestr(f"f{i}.txt", "x")
    return path


def _zip_entry(z: zipfile.ZipFile, name: str, data: bytes, mode: int | None = None) -> None:
    zi = zipfile.ZipInfo(name)
    if mode is not None:
        zi.external_attr = mode << 16
    z.writestr(zi, data)


def make_traversal_zip(path: Path, name: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        _zip_entry(z, "ok.txt", b"fine")
        _zip_entry(z, name, b"owned")
    return path


def make_symlink_zip(path: Path, target: str, name: str = "link") -> Path:
    with zipfile.ZipFile(path, "w") as z:
        _zip_entry(z, name, target.encode(), stat.S_IFLNK | 0o777)
    return path


def make_nested_zip(path: Path) -> Path:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("inner/secret_code.py", "INNER = 1\n")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("main.py", "x = 1\n")
        z.writestr("payload.zip", inner.getvalue())
    return path


def _tar_add(tf: tarfile.TarFile, name: str, *, type_: bytes = tarfile.REGTYPE, data: bytes = b"", linkname: str = "") -> None:
    ti = tarfile.TarInfo(name)
    ti.type = type_
    ti.linkname = linkname
    ti.size = len(data) if type_ == tarfile.REGTYPE else 0
    if type_ in (tarfile.CHRTYPE, tarfile.BLKTYPE):
        ti.devmajor, ti.devminor = 1, 3
    tf.addfile(ti, io.BytesIO(data) if type_ == tarfile.REGTYPE else None)


def make_tar(path: Path, entries: list[dict]) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for e in entries:
            _tar_add(tf, **e)
    return path


def make_tar_bomb(path: Path, mib: int = 24) -> Path:
    return make_tar(path, [dict(name="bomb.txt", data=b"\0" * (mib * 1024 * 1024))])


# --------------------------------------------------------------- directory
def make_hostile_directory(root: Path, outside: Path) -> None:
    """Directory source with symlink escapes, loops, FIFO. `outside` holds a file that must not leak."""
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "host_secret.txt").write_text("HOST-FILE-CONTENT\n")
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("x = 1\n")
    os.symlink(outside / "host_secret.txt", root / "leak_file")
    os.symlink(outside, root / "leak_dir")
    os.symlink("/etc/passwd", root / "passwd_link")
    os.symlink("loop_b", root / "loop_a")
    os.symlink("loop_a", root / "loop_b")
    os.symlink(".", root / "self_dir")
    if hasattr(os, "mkfifo"):
        os.mkfifo(root / "pipe.fifo")


# --------------------------------------------------------------------- git
def _git(*args: str, cwd: Path | None = None, env: dict | None = None) -> None:
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
         "GIT_COMMITTER_EMAIL": "t@x", **(env or {})}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=e)


def make_git_repo(base: Path, marker: Path) -> Path:
    """Local bare repo (no network). Source repo has hostile hooks, .gitmodules and an LFS .gitattributes.
    Hooks are never transferred by clone; the fixture proves they are not executed either way."""
    work = base / "work"
    work.mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=work)
    hook = f"#!/bin/sh\necho pwned > '{marker}'\n"
    for name in ("post-checkout", "post-merge", "pre-commit"):
        h = work / ".git" / "hooks" / name
        h.write_text(hook)
        h.chmod(0o755)
    (work / "app.py").write_text("print('hi')\n")
    (work / ".gitmodules").write_text(f'[submodule "evil"]\n\tpath = evil\n\turl = ssh://127.0.0.1:1/evil.git\n')
    (work / ".gitattributes").write_text("*.bin filter=lfs diff=lfs merge=lfs -text\n")
    (work / "big.bin").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 5\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "--no-verify", "-m", "init", cwd=work, env={})
    bare = base / "repo.git"
    _git("clone", "-q", "--bare", str(work), str(bare))
    # also plant a hook in the bare repo itself
    h = bare / "hooks" / "post-checkout"
    h.parent.mkdir(exist_ok=True)
    h.write_text(hook)
    h.chmod(0o755)
    return bare


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "redteam_out")
    out.mkdir(parents=True, exist_ok=True)
    make_secret_repo(out / "secret_repo")
    make_injection_repo(out / "injection_repo")
    make_zip_bomb(out / "bomb.zip")
    make_traversal_zip(out / "traversal.zip", "../../evil.txt")
    make_nested_zip(out / "nested.zip")
    print(f"wrote fixtures to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
