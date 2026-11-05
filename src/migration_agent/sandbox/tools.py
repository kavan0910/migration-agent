"""Path-jailed, read-only tool set for agents.

This class intentionally exposes NO write, exec or network functionality.
Every path is resolved (following symlinks) and must stay inside ``root``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel


class SandboxViolation(Exception):
    """Raised when a tool call would break the sandbox policy."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class ReadResult(BaseModel):
    path: str
    start: int
    end: int
    total_lines: int
    truncated: bool
    content: str


class DirEntry(BaseModel):
    name: str
    kind: str  # "file" | "dir" | "symlink-file" | "symlink-dir" | "other"
    size: int | None = None


class ListResult(BaseModel):
    path: str
    entries: list[DirEntry]
    truncated: bool


class GrepMatch(BaseModel):
    path: str
    line: int
    text: str


class GrepResult(BaseModel):
    pattern: str
    matches: list[GrepMatch]
    files_scanned: int
    truncated: bool


class StatResult(BaseModel):
    path: str
    kind: str
    size: int
    is_binary: bool | None = None
    mtime: float


class ReadOnlyTools:
    MAX_FILE_BYTES = 2_000_000  # refuse to read files bigger than this
    MAX_READ_LINES = 2000
    MAX_LINE_CHARS = 2000
    MAX_OUTPUT_CHARS = 200_000
    MAX_LIST_ENTRIES = 1000
    MAX_GREP_MATCHES = 200
    MAX_GREP_FILES = 5000
    MAX_PATTERN_CHARS = 500

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError(f"root is not a directory: {root}")

    @property
    def root(self) -> Path:
        return self._root

    # -- path jail ---------------------------------------------------------
    def _resolve(self, path: str | os.PathLike[str] | None) -> Path:
        raw = "." if path in (None, "") else os.fspath(path)
        if "\x00" in raw:
            raise SandboxViolation("NUL byte in path", path=raw)
        p = Path(raw)
        if ".." in p.parts:
            raise SandboxViolation("path traversal ('..') is not allowed", path=raw)
        candidate = p if p.is_absolute() else self._root / p
        try:
            resolved = candidate.resolve(strict=False)  # follows symlinks
        except (OSError, RuntimeError) as e:
            raise SandboxViolation(f"cannot resolve path: {e}", path=raw) from e
        if resolved != self._root and not resolved.is_relative_to(self._root):
            raise SandboxViolation("path escapes sandbox root", path=raw)
        return resolved

    def _rel(self, p: Path) -> str:
        return "." if p == self._root else p.relative_to(self._root).as_posix()

    @staticmethod
    def _looks_binary(head: bytes) -> bool:
        if b"\x00" in head:
            return True
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            # tolerate a multibyte char cut at the end of the sample
            return e.start < len(head) - 4
        return False

    def _check_text_file(self, p: Path, shown: str) -> None:
        if not p.exists():
            raise SandboxViolation("no such file", path=shown)
        if not p.is_file():
            raise SandboxViolation("not a regular file", path=shown)
        if p.stat().st_size > self.MAX_FILE_BYTES:
            raise SandboxViolation(
                f"file exceeds size cap of {self.MAX_FILE_BYTES} bytes", path=shown
            )
        with p.open("rb") as f:
            if self._looks_binary(f.read(8192)):
                raise SandboxViolation("binary file refused", path=shown)

    # -- tools -------------------------------------------------------------
    def read_file(self, path: str, start: int | None = None, end: int | None = None) -> ReadResult:
        """Read lines [start, end] (1-based, inclusive)."""
        p = self._resolve(path)
        self._check_text_file(p, path)
        s = 1 if start is None else start
        if s < 1 or (end is not None and end < s):
            raise SandboxViolation("invalid line range", path=path)
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(lines)
        e = total if end is None else min(end, total)
        truncated = False
        if e - s + 1 > self.MAX_READ_LINES:
            e = s + self.MAX_READ_LINES - 1
            truncated = True
        out: list[str] = []
        size = 0
        for line in lines[s - 1 : e]:
            if len(line) > self.MAX_LINE_CHARS:
                line = line[: self.MAX_LINE_CHARS] + "...[line truncated]"
                truncated = True
            size += len(line) + 1
            if size > self.MAX_OUTPUT_CHARS:
                truncated = True
                break
            out.append(line)
        return ReadResult(
            path=self._rel(p), start=s, end=s + len(out) - 1 if out else s - 1,
            total_lines=total, truncated=truncated or e < (total if end is None else min(end, total)),
            content="\n".join(out),
        )

    def list_dir(self, path: str | None = None) -> ListResult:
        p = self._resolve(path)
        if not p.is_dir():
            raise SandboxViolation("not a directory", path=str(path))
        entries: list[DirEntry] = []
        truncated = False
        for child in sorted(p.iterdir(), key=lambda c: c.name):
            if len(entries) >= self.MAX_LIST_ENTRIES:
                truncated = True
                break
            link = child.is_symlink()
            if link:
                # never reveal targets; flag links that escape the root
                try:
                    tgt = child.resolve(strict=False)
                    inside = tgt == self._root or tgt.is_relative_to(self._root)
                except (OSError, RuntimeError):
                    inside = False
                if not inside:
                    entries.append(DirEntry(name=child.name, kind="symlink-escaping"))
                    continue
            if child.is_dir():
                kind, size = ("symlink-dir" if link else "dir"), None
            elif child.is_file():
                kind, size = ("symlink-file" if link else "file"), child.stat().st_size
            else:
                kind, size = "other", None
            entries.append(DirEntry(name=child.name, kind=kind, size=size))
        return ListResult(path=self._rel(p), entries=entries, truncated=truncated)

    def grep(self, pattern: str, path: str | None = None) -> GrepResult:
        if len(pattern) > self.MAX_PATTERN_CHARS:
            raise SandboxViolation("pattern too long")
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise SandboxViolation(f"invalid regex: {e}") from e
        base = self._resolve(path)
        if not base.exists():
            raise SandboxViolation("no such path", path=str(path))
        matches: list[GrepMatch] = []
        scanned = 0
        truncated = False

        def files():
            if base.is_file():
                yield base
                return
            for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                dirnames.sort()
                for fn in sorted(filenames):
                    yield Path(dirpath) / fn

        for f in files():
            if scanned >= self.MAX_GREP_FILES or len(matches) >= self.MAX_GREP_MATCHES:
                truncated = True
                break
            try:
                rp = f.resolve(strict=True)
                if not rp.is_relative_to(self._root):
                    continue  # skip escaping symlinks silently
                self._check_text_file(rp, self._rel(rp))
            except (SandboxViolation, OSError):
                continue  # skip binary / oversized / unreadable
            scanned += 1
            text = rp.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    matches.append(GrepMatch(
                        path=self._rel(rp), line=i, text=line[: self.MAX_LINE_CHARS]))
                    if len(matches) >= self.MAX_GREP_MATCHES:
                        truncated = True
                        break
        return GrepResult(pattern=pattern, matches=matches, files_scanned=scanned, truncated=truncated)

    def file_stat(self, path: str) -> StatResult:
        p = self._resolve(path)
        if not p.exists():
            raise SandboxViolation("no such path", path=path)
        st = p.stat()
        kind = "dir" if p.is_dir() else "file" if p.is_file() else "other"
        binary = None
        if kind == "file":
            with p.open("rb") as f:
                binary = self._looks_binary(f.read(8192))
        return StatResult(path=self._rel(p), kind=kind, size=st.st_size,
                          is_binary=binary, mtime=st.st_mtime)
