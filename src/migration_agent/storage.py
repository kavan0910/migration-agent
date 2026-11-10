"""Project archives for hosted use (engine seam E1/E2): pack a scratch project directory into a deterministic
tar.gz and unpack one safely. A stateless worker downloads the archive, unpacks it into scratch, runs stages via
`migration_agent.service`, packs again and publishes the result.

Safety on unpack: only regular files and directories are accepted. Absolute paths, `..`, backslashes, NUL bytes,
drive letters, links, devices, FIFOs and duplicate entries are rejected, as are archives over the size caps.
Extraction is streaming, so a decompression bomb is cut off at the byte cap instead of being expanded first.
"""
from __future__ import annotations

import gzip
import io
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath

CACHE_DIR = "llm_cache"
MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024        # compressed input
MAX_UNPACKED_BYTES = 4 * 1024 * 1024 * 1024   # sum of regular-file sizes
MAX_MEMBERS = 250_000


class ArchiveError(ValueError):
    """The archive is unsafe or exceeds a size cap."""


def pack_project(project_dir: Path, *, include_cache: bool = True) -> bytes:
    """Deterministic .tar.gz of `project_dir` (sorted entries, zeroed owner/mtime, fixed modes, gzip mtime 0).
    Symlinks and special files are skipped. `llm_cache/` is dropped when `include_cache` is False."""
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise ArchiveError(f"not a directory: {root}")
    entries: list[tuple[str, Path, bool]] = []
    for dp, dns, fns in os.walk(root, followlinks=False):
        rel_dir = Path(dp).relative_to(root)
        dns[:] = sorted(d for d in dns if not (not include_cache and rel_dir == Path(".") and d == CACHE_DIR))
        for d in dns:
            full = Path(dp, d)
            if not full.is_symlink():
                entries.append((PurePosixPath(rel_dir, d).as_posix(), full, True))
        for fn in sorted(fns):
            full = Path(dp, fn)
            if full.is_symlink() or not full.is_file():
                continue
            entries.append((PurePosixPath(rel_dir, fn).as_posix(), full, False))
    entries.sort(key=lambda e: e[0])
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=6) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tf:
            for arc, full, is_dir in entries:
                ti = tarfile.TarInfo(arc)
                ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = 0, 0, 0, "", ""
                if is_dir:
                    ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                    tf.addfile(ti)
                else:
                    ti.size = full.stat().st_size
                    ti.mode = 0o755 if os.access(full, os.X_OK) else 0o644
                    with open(full, "rb") as f:
                        tf.addfile(ti, f)
    return buf.getvalue()


def _clean_name(name: str) -> PurePosixPath | None:
    if not name or "\x00" in name or "\\" in name:
        raise ArchiveError(f"unsafe path in archive: {name!r}")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise ArchiveError(f"absolute path in archive: {name!r}")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ArchiveError(f"path traversal in archive: {name!r}")
    return PurePosixPath(*parts) if parts else None


def unpack_project(data: bytes, dest: Path, *, max_unpacked_bytes: int = MAX_UNPACKED_BYTES,
                   max_members: int = MAX_MEMBERS, max_archive_bytes: int = MAX_ARCHIVE_BYTES) -> None:
    """Validate and extract `data` into `dest` (must not exist or be empty). On any error `dest` is restored
    to its previous state (removed if it was created here) and ArchiveError is raised."""
    if len(data) > max_archive_bytes:
        raise ArchiveError(f"archive larger than {max_archive_bytes} bytes")
    dest = Path(dest)
    if dest.is_symlink():
        raise ArchiveError("destination is a symlink")
    created = not dest.exists()
    if not created and (not dest.is_dir() or any(dest.iterdir())):
        raise ArchiveError(f"destination is not empty: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    try:
        _extract(data, root, max_unpacked_bytes, max_members)
    except ArchiveError:
        _undo(dest, created)
        raise
    except (tarfile.TarError, EOFError, OSError, gzip.BadGzipFile, UnicodeError) as e:
        _undo(dest, created)
        raise ArchiveError(f"invalid archive: {type(e).__name__}: {e}") from e


def _undo(dest: Path, created: bool) -> None:
    if created:
        shutil.rmtree(dest, ignore_errors=True)
    else:
        for child in dest.iterdir():
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() and not child.is_symlink() else child.unlink(missing_ok=True)


def _extract(data: bytes, root: Path, max_bytes: int, max_members: int) -> None:
    total = 0
    count = 0
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for m in tf:
            count += 1
            if count > max_members:
                raise ArchiveError(f"too many entries (> {max_members})")
            if not (m.isreg() or m.isdir()):
                kind = "symlink" if m.issym() else "hardlink" if m.islnk() else "special file"
                raise ArchiveError(f"{kind} not allowed in archive: {m.name!r}")
            rel = _clean_name(m.name)
            if rel is None:
                continue
            key = rel.as_posix()
            target = root.joinpath(*rel.parts)
            if root not in target.resolve(strict=False).parents and target.resolve(strict=False) != root:
                raise ArchiveError(f"path escapes destination: {m.name!r}")
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if key in seen:
                raise ArchiveError(f"duplicate entry: {m.name!r}")
            seen.add(key)
            if m.size < 0 or total + m.size > max_bytes:
                raise ArchiveError(f"unpacked size exceeds {max_bytes} bytes")
            total += m.size
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            assert src is not None
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
