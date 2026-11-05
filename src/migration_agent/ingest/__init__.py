"""Ingestion engine (A-02): turn a directory/archive/git URL into a normalized code/ dir.

Nothing from the source is ever executed. Archives are validated before and during
extraction; git clones run with hooks disabled and a protocol allowlist.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, Field

from .secrets import SecretFinding, scan_text

__all__ = ["ingest", "IngestReport", "IngestError", "Exclusion", "SecretFinding"]

MAX_BYTES = 500 * 1024 * 1024
MAX_FILES = 200_000
MAX_RATIO = 100
MAX_FILE_BYTES = 1024 * 1024

EXCLUDED_DIRS = {
    ".git": "vcs", "node_modules": "dependencies", "vendor": "dependencies",
    "build": "build_output", "dist": "build_output", "target": "build_output",
    "__pycache__": "cache", ".venv": "dependencies", "venv": "dependencies",
    ".tox": "cache", ".mypy_cache": "cache", ".pytest_cache": "cache",
}
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".zip", ".gz", ".tgz",
    ".tar", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war", ".class", ".so", ".dll", ".dylib",
    ".exe", ".o", ".a", ".pyc", ".pyo", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
    ".mov", ".avi", ".bin", ".dat", ".db", ".sqlite", ".lock",
}


class IngestError(Exception):
    """Source rejected (unsafe, too large, malformed, or unreachable)."""


class Exclusion(BaseModel):
    path: str
    reason: str  # vcs|dependencies|build_output|cache|binary|too_large|symlink
    detail: str = ""


class IngestReport(BaseModel):
    source: str
    source_type: str  # directory|zip|tar|git
    source_hash: str  # sha256: archive bytes, or content tree hash for dir/git
    commit: str | None = None
    files_copied: int = 0
    bytes_copied: int = 0
    files_skipped: int = 0
    exclusions: list[Exclusion] = Field(default_factory=list)
    secret_findings: list[SecretFinding] = Field(default_factory=list)
    files_with_secrets: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- helpers
def _safe_rel(name: str) -> PurePosixPath:
    n = name.replace("\\", "/")
    p = PurePosixPath(n)
    if p.is_absolute() or n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        raise IngestError(f"unsafe archive entry (absolute path): {name!r}")
    if ".." in p.parts:
        raise IngestError(f"unsafe archive entry (path traversal): {name!r}")
    if "\x00" in n:
        raise IngestError(f"unsafe archive entry (NUL byte): {name!r}")
    return p


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_limits(total: int, count: int, max_bytes: int, max_files: int) -> None:
    if count > max_files:
        raise IngestError(f"too many files: {count} > limit {max_files}")
    if total > max_bytes:
        raise IngestError(f"uncompressed size {total} bytes exceeds limit {max_bytes}")


def _copy_limited(src, dst: Path, budget: list[int]) -> None:
    """Stream src to dst, enforcing the remaining byte budget on actual bytes."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as out:
        while chunk := src.read(1 << 16):
            budget[0] -= len(chunk)
            if budget[0] < 0:
                raise IngestError("extracted size exceeds limit (declared sizes were understated; possible zip bomb)")
            out.write(chunk)


# ------------------------------------------------------------- extraction
def _extract_zip(archive: Path, out: Path, max_bytes: int, max_files: int, max_ratio: int) -> None:
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as e:
        raise IngestError(f"invalid zip archive: {e}") from e
    with zf:
        infos = zf.infolist()
        total = sum(i.file_size for i in infos)
        _check_limits(total, len(infos), max_bytes, max_files)
        for i in infos:
            if i.compress_size and i.file_size / i.compress_size > max_ratio and i.file_size > 1024 * 1024:
                raise IngestError(
                    f"compression ratio {i.file_size // i.compress_size}:1 for {i.filename!r} exceeds limit {max_ratio}:1 (zip bomb?)"
                )
        csize = archive.stat().st_size or 1
        if total > 10 * 1024 * 1024 and total / csize > max_ratio:
            raise IngestError(f"overall compression ratio {total // csize}:1 exceeds limit {max_ratio}:1 (zip bomb?)")
        budget = [max_bytes]
        for i in infos:
            rel = _safe_rel(i.filename)
            mode = (i.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                target = zf.read(i).decode("utf-8", "replace")
                _check_link(rel, target)
                continue  # internal symlinks are not materialised
            if i.is_dir():
                continue
            with zf.open(i) as src:
                _copy_limited(src, out / rel, budget)


def _check_link(rel: PurePosixPath, target: str) -> None:
    t = PurePosixPath(target.replace("\\", "/"))
    if t.is_absolute():
        raise IngestError(f"unsafe symlink {str(rel)!r} -> {target!r} (absolute target)")
    resolved = os.path.normpath(str(rel.parent / t))
    if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
        raise IngestError(f"unsafe symlink {str(rel)!r} -> {target!r} (escapes archive root)")


def _extract_tar(archive: Path, out: Path, max_bytes: int, max_files: int, max_ratio: int) -> None:
    try:
        tf = tarfile.open(archive)
    except tarfile.TarError as e:
        raise IngestError(f"invalid tar archive: {e}") from e
    csize = archive.stat().st_size or 1
    with tf:
        total = count = 0
        budget = [max_bytes]
        for m in tf:
            count += 1
            total += max(m.size, 0)
            _check_limits(total, count, max_bytes, max_files)
            if total > 10 * 1024 * 1024 and total / csize > max_ratio:
                raise IngestError(f"compression ratio {total // csize}:1 exceeds limit {max_ratio}:1 (zip bomb?)")
            rel = _safe_rel(m.name)
            if m.issym() or m.islnk():
                if m.islnk():
                    _safe_rel(m.linkname)  # hardlink targets are archive-root relative
                else:
                    _check_link(rel, m.linkname)
                continue
            if m.isdir():
                continue
            if not m.isfile():
                raise IngestError(f"unsafe archive entry (special file): {m.name!r}")
            src = tf.extractfile(m)
            assert src is not None
            _copy_limited(src, out / rel, budget)


# ------------------------------------------------------------------- git
_GIT_PREFIXES = ("https://", "ssh://", "file://", "git@", "http://", "git://", "ext::", "ftp://")


def _is_git_url(s: str) -> bool:
    return s.lower().startswith(_GIT_PREFIXES)


def _git_clone(url: str, out: Path) -> str:
    low = url.lower()
    if not low.startswith(("https://", "ssh://", "file://", "git@")):
        raise IngestError(f"git protocol not allowed (only https, ssh, file): {url!r}")
    if url.startswith("-"):
        raise IngestError("invalid git URL")
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_ALLOW_PROTOCOL": "https:ssh:file",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    cmd = [
        "git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always", "-c", "protocol.ssh.allow=always",
        "-c", "protocol.file.allow=always", "-c", "core.fsmonitor=false",
        "clone", "--depth", "1", "--no-tags", "--single-branch", "--", url, str(out),
    ]
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise IngestError(f"git clone failed: {e}") from e
    if r.returncode != 0:
        raise IngestError(f"git clone failed: {r.stderr.strip()[:500]}")
    head = subprocess.run(
        ["git", "-C", str(out), "rev-parse", "HEAD"], env=env, capture_output=True, text=True
    )
    return head.stdout.strip()


# ------------------------------------------------------------- normalize
def _is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return True
    with open(path, "rb") as f:
        return b"\x00" in f.read(8192)


def _normalize(root: Path, dest: Path, report: IngestReport, max_bytes: int, max_files: int) -> str:
    """Walk root, apply exclusions, copy to dest, scan secrets. Returns tree hash."""
    tree = hashlib.sha256()
    seen_files = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        keep = []
        for name in sorted(dirnames):
            full = d / name
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                report.exclusions.append(Exclusion(path=rel, reason="symlink", detail="symlinked directory not followed"))
            elif name in EXCLUDED_DIRS:
                report.exclusions.append(Exclusion(path=rel + "/", reason=EXCLUDED_DIRS[name], detail=f"directory {name}"))
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            full = d / name
            rel = full.relative_to(root).as_posix()
            seen_files += 1
            if seen_files > max_files:
                raise IngestError(f"too many files: more than {max_files}")
            if full.is_symlink():
                report.exclusions.append(Exclusion(path=rel, reason="symlink", detail="symlink not copied"))
                continue
            size = full.stat().st_size
            if size > MAX_FILE_BYTES:
                report.exclusions.append(Exclusion(path=rel, reason="too_large", detail=f"{size} bytes > {MAX_FILE_BYTES}"))
                continue
            if _is_binary(full):
                report.exclusions.append(Exclusion(path=rel, reason="binary"))
                continue
            if report.bytes_copied + size > max_bytes:
                raise IngestError(f"total size exceeds limit {max_bytes}")
            data = full.read_bytes()
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            report.files_copied += 1
            report.bytes_copied += len(data)
            tree.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
            found = scan_text(rel, data.decode("utf-8", "replace"))
            if found:
                report.secret_findings.extend(found)
                report.files_with_secrets.append(rel)
    report.files_skipped = len(report.exclusions)
    return tree.hexdigest()


# ---------------------------------------------------------------- public
def ingest(
    source: str,
    dest_code_dir: Path,
    *,
    max_bytes: int = MAX_BYTES,
    max_files: int = MAX_FILES,
    max_ratio: int = MAX_RATIO,
) -> IngestReport:
    """Ingest a directory, .zip, .tar/.tar.gz/.tgz or git URL into dest_code_dir."""
    dest = Path(dest_code_dir)
    if dest.exists() and any(dest.iterdir()):
        raise IngestError(f"destination is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    src = str(source)

    with tempfile.TemporaryDirectory(prefix="ingest-") as tmp:
        stage = Path(tmp) / "stage"
        commit = None
        if _is_git_url(src):
            stype = "git"
            commit = _git_clone(src, stage)
            root, src_hash = stage, None
        else:
            p = Path(src).expanduser()
            if not p.exists():
                raise IngestError(f"source not found: {src}")
            if p.is_dir():
                stype, root, src_hash = "directory", p, None
            else:
                stage.mkdir()
                lower = p.name.lower()
                src_hash = _sha_file(p)
                if lower.endswith(".zip"):
                    stype = "zip"
                    _extract_zip(p, stage, max_bytes, max_files, max_ratio)
                elif lower.endswith((".tar", ".tar.gz", ".tgz")):
                    stype = "tar"
                    _extract_tar(p, stage, max_bytes, max_files, max_ratio)
                else:
                    raise IngestError(f"unsupported source type: {p.name}")
                root = stage

        report = IngestReport(source=src, source_type=stype, source_hash="", commit=commit)
        try:
            tree_hash = _normalize(root, dest, report, max_bytes, max_files)
        except IngestError:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        report.source_hash = src_hash or tree_hash
    return report
