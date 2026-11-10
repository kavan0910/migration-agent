#!/usr/bin/env python3
"""Release helper (D-03): verify, build, checksum, and print (never run) signing commands.

    python scripts/release.py v0.2.0            # checks + build + SHA256SUMS in dist/
    python scripts/release.py v0.2.0 --check    # checks only
    python scripts/release.py v0.2.0 --allow-dirty   # for dry runs only

Requires `pip install build`. Signing is never invoked automatically: the cosign
commands are printed for you (or CI) to run deliberately.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-(rc|alpha|beta)\.?(\d+))?$")
VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)


class ReleaseError(Exception):
    pass


def parse_tag(tag: str) -> str:
    """'v1.2.3' / 'v1.2.3-rc1' -> PEP 440 version ('1.2.3' / '1.2.3rc1'). Raises on bad format."""
    m = TAG_RE.match(tag)
    if not m:
        raise ReleaseError(f"bad tag {tag!r}: expected vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCH-rcN")
    major, minor, patch, pre, n = m.groups()
    base = f"{major}.{minor}.{patch}"
    if pre:
        base += {"rc": "rc", "alpha": "a", "beta": "b"}[pre] + n
    return base


def read_version(pyproject_text: str) -> str:
    m = VERSION_RE.search(pyproject_text)
    if not m:
        raise ReleaseError("no version found in pyproject.toml")
    return m.group(1)


def check_tag_matches(tag: str, pyproject_text: str) -> str:
    want, have = parse_tag(tag), read_version(pyproject_text)
    if want != have:
        raise ReleaseError(f"tag {tag} implies version {want} but pyproject.toml says {have}")
    return have


def check_clean(porcelain: str) -> None:
    dirty = [ln for ln in porcelain.splitlines() if ln.strip()]
    if dirty:
        raise ReleaseError("working tree not clean:\n  " + "\n  ".join(dirty[:20]))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def format_sha256sums(entries: dict[str, str]) -> str:
    """`sha256sum -c` compatible text (two spaces, sorted by file name)."""
    return "".join(f"{digest}  {name}\n" for name, digest in sorted(entries.items()))


def cosign_commands(names: list[str], dist: str = "dist") -> list[str]:
    cmds = [f"cosign sign-blob --yes --bundle {dist}/{n}.sigstore.json {dist}/{n}" for n in sorted(names)]
    cmds.append("# image: cosign sign --yes ghcr.io/kavan0910/migration-agent@sha256:<digest>   (keyless, needs OIDC)")
    return cmds


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tag")
    ap.add_argument("--check", action="store_true", help="run checks only; do not build")
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args(argv)
    try:
        version = check_tag_matches(a.tag, (ROOT / "pyproject.toml").read_text())
        if not a.allow_dirty:
            check_clean(_git("status", "--porcelain"))
    except (ReleaseError, subprocess.CalledProcessError) as e:
        print(f"release check failed: {e}", file=sys.stderr)
        return 1
    print(f"ok: tag {a.tag} == version {version}")
    if a.check:
        return 0
    dist = ROOT / "dist"
    for old in dist.glob("*") if dist.exists() else []:
        old.unlink()
    r = subprocess.run([sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(dist), str(ROOT)])
    if r.returncode:
        print("build failed (is `build` installed? pip install build)", file=sys.stderr)
        return 1
    files = sorted(p for p in dist.iterdir() if p.suffix == ".whl" or p.name.endswith(".tar.gz"))
    (dist / "SHA256SUMS").write_text(format_sha256sums({p.name: sha256_file(p) for p in files}))
    print(f"wrote {dist / 'SHA256SUMS'}\n\nTo sign (NOT run automatically):")
    print("\n".join("  " + c for c in cosign_commands([p.name for p in files])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
