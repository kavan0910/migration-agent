"""Untrusted-input guards: SSRF validation for git URLs and safe archive handling (zip slip, bombs, links)."""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable
from urllib.parse import urlsplit


class UnsafeInput(Exception):
    """Raised for anything that violates ingestion policy. Message is safe to show to the user."""


# ----------------------------------------------------------------------------------------------- SSRF

_EXTRA_BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24",
    "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4",
    "240.0.0.0/4", "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8", "64:ff9b::/96", "2001:db8::/32",
    "fd00:ec2::254/128",
)]
_NUMERIC_TLD = re.compile(r"(0x[0-9a-f]*|[0-9]+)")
_BAD_HOSTS = {"localhost", "metadata.google.internal", "metadata", "instance-data"}
_BAD_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa", ".lan", ".corp", ".intranet")


def ip_is_public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv6Address) and ip.sixtofour is not None:
        if not ip_is_public(ip.sixtofour):
            return False
    if not ip.is_global:
        return False
    return not any(ip in net for net in _EXTRA_BLOCKED if net.version == ip.version)


@dataclass(frozen=True)
class SafeGitUrl:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


Resolver = Callable[[str, int], list[str]]


def default_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise UnsafeInput("host could not be resolved") from None
    return sorted({i[4][0] for i in infos})


def validate_git_url(url: str, *, allowed_hosts: set[str] | None = None, resolver: Resolver = default_resolver) -> SafeGitUrl:
    """https only, public hosts only. Resolves DNS and rejects if ANY address is non-public."""
    if not isinstance(url, str) or not url or len(url) > 2000:
        raise UnsafeInput("invalid git url")
    if any(ord(c) < 33 or ord(c) == 127 for c in url) or "\\" in url:
        raise UnsafeInput("invalid git url")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UnsafeInput("invalid git url") from None
    if parts.scheme != "https":
        raise UnsafeInput("only https git urls are allowed")
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        raise UnsafeInput("credentials in url are not allowed")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or host.startswith("-"):
        raise UnsafeInput("invalid git url host")
    if port not in (None, 443):
        raise UnsafeInput("only port 443 is allowed")
    if parts.fragment:
        raise UnsafeInput("fragments are not allowed in git urls")
    if host in _BAD_HOSTS or host.endswith(_BAD_SUFFIXES):
        raise UnsafeInput("host is not allowed")
    if allowed_hosts and host not in allowed_hosts:
        raise UnsafeInput("host is not on the allowlist")
    try:  # literal IP (including odd forms normalised by ipaddress)
        lit = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        lit = None
        # inet_aton-style spellings (127.1, 2130706433, 0x7f.0.0.1, 0177.0.0.1) are never real hostnames: a TLD is
        # not numeric. Reject before DNS so no resolver quirk can turn them into 127.0.0.1.
        if _NUMERIC_TLD.fullmatch(host.rsplit(".", 1)[-1]):
            raise UnsafeInput("numeric host names are not allowed") from None
    if lit is not None:
        if not ip_is_public(lit):
            raise UnsafeInput("address is not public")
        addrs = [str(lit)]
    else:
        addrs = resolver(host, 443)
    if not addrs:
        raise UnsafeInput("host could not be resolved")
    for a in addrs:
        try:
            ip = ipaddress.ip_address(a.split("%")[0])
        except ValueError:
            raise UnsafeInput("host resolved to an invalid address") from None
        if not ip_is_public(ip):
            raise UnsafeInput("host resolves to a non-public address")
    return SafeGitUrl(url=url, host=host, port=443, addresses=tuple(addrs))


# ----------------------------------------------------------------------------------------------- archives

@dataclass
class ArchiveLimits:
    max_bytes: int = 500 * 1024 * 1024       # total uncompressed
    max_file_bytes: int = 50 * 1024 * 1024
    max_files: int = 50_000
    max_ratio: int = 200                     # uncompressed / compressed for the whole archive
    max_depth: int = 64
    max_name_len: int = 1024


@dataclass
class ArchiveStats:
    files: int = 0
    bytes: int = 0


_WIN_DRIVE = re.compile(r"^[A-Za-z]:")


def safe_member_path(name: str, limits: ArchiveLimits) -> PurePosixPath:
    if not name or len(name) > limits.max_name_len or "\x00" in name:
        raise UnsafeInput("archive contains an invalid entry name")
    if "\\" in name or name.startswith("/") or _WIN_DRIVE.match(name):
        raise UnsafeInput("archive contains an absolute or backslash path")
    p = PurePosixPath(name)
    if any(part in ("..",) for part in p.parts):
        raise UnsafeInput("archive contains a path traversal entry")
    if len(p.parts) > limits.max_depth:
        raise UnsafeInput("archive paths are nested too deeply")
    return p


def _copy_capped(src: BinaryIO, dst: BinaryIO | None, budget: list[int], per_file: int) -> int:
    n = 0
    while True:
        chunk = src.read(1024 * 256)
        if not chunk:
            return n
        n += len(chunk)
        budget[0] -= len(chunk)
        if n > per_file:
            raise UnsafeInput("archive contains a file that is too large")
        if budget[0] < 0:
            raise UnsafeInput("archive expands beyond the allowed size")
        if dst is not None:
            dst.write(chunk)


def _sniff(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] == b"PK":
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "tar.gz"
    raise UnsafeInput("unsupported archive type (use .zip or .tar.gz)")


def extract_archive(archive: Path, dest: Path | None, limits: ArchiveLimits) -> ArchiveStats:
    """Validate (dest=None) or extract an archive. Sizes are counted from the real decompressed stream, never from
    headers. Symlinks, hardlinks, devices and special files are rejected outright."""
    kind = _sniff(archive)
    compressed = max(archive.stat().st_size, 1)
    stats = ArchiveStats()
    budget = [limits.max_bytes]
    root = dest.resolve() if dest is not None else None
    if dest is not None:
        dest.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()

    def target_for(name: str) -> Path | None:
        rel = safe_member_path(name, limits)
        if root is None:
            return None
        t = (root / rel.as_posix()).resolve()
        if root != t and root not in t.parents:
            raise UnsafeInput("archive contains a path traversal entry")
        return t

    def account(name: str) -> None:
        key = name.lower()
        if key in seen:
            raise UnsafeInput("archive contains duplicate entries")
        seen.add(key)
        stats.files += 1
        if stats.files > limits.max_files:
            raise UnsafeInput("archive contains too many files")

    def write(opener: Callable[[], BinaryIO], target: Path | None) -> None:
        with opener() as src:
            if target is None:
                n = _copy_capped(src, None, budget, limits.max_file_bytes)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
                with os.fdopen(fd, "wb") as out:
                    n = _copy_capped(src, out, budget, limits.max_file_bytes)
        stats.bytes += n
        if stats.bytes > compressed * limits.max_ratio and stats.bytes > 10 * 1024 * 1024:
            raise UnsafeInput("archive compression ratio is suspiciously high")

    try:
        if kind == "zip":
            with zipfile.ZipFile(archive) as zf:
                infos = zf.infolist()
                if len(infos) > limits.max_files:
                    raise UnsafeInput("archive contains too many files")
                for zi in infos:
                    mode = (zi.external_attr >> 16) & 0xFFFF
                    if zi.is_dir():
                        t = target_for(zi.filename.rstrip("/") or zi.filename)
                        if t is not None:
                            t.mkdir(parents=True, exist_ok=True)
                        continue
                    if (mode & 0o170000) and not stat.S_ISREG(mode):
                        raise UnsafeInput("archive contains links or special files")
                    if zi.flag_bits & 0x1:
                        raise UnsafeInput("encrypted archives are not supported")
                    t = target_for(zi.filename)
                    account(zi.filename)
                    write(lambda zi=zi: zf.open(zi), t)
        else:
            with tarfile.open(archive, mode="r:gz") as tf:
                for ti in tf:
                    if ti.isdir():
                        t = target_for(ti.name.rstrip("/") or ti.name)
                        if t is not None:
                            t.mkdir(parents=True, exist_ok=True)
                        continue
                    if not ti.isreg():
                        raise UnsafeInput("archive contains links or special files")
                    t = target_for(ti.name)
                    account(ti.name)
                    write(lambda ti=ti: tf.extractfile(ti), t)  # type: ignore[arg-type,return-value]
    except UnsafeInput:
        raise
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, RuntimeError, NotImplementedError) as e:
        raise UnsafeInput(f"archive is corrupt or unsupported ({type(e).__name__})") from None
    if stats.files == 0:
        raise UnsafeInput("archive is empty")
    return stats


def sanitize_filename(name: str, max_len: int = 200) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(". ")
    if not base or len(base) > max_len:
        raise UnsafeInput("invalid file name")
    return base


def strip_unsafe_tree(root: Path) -> int:
    """Remove symlinks and non-regular files from a directory tree (used after git clone). Returns removed count."""
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for n in list(dirnames) + list(filenames):
            p = Path(dirpath) / n
            if p.is_symlink() or (not p.is_dir() and not p.is_file()):
                p.unlink(missing_ok=True)
                removed += 1
    return removed


def dir_size(root: Path) -> tuple[int, int]:
    total = files = 0
    for dirpath, _, filenames in os.walk(root, followlinks=False):
        for n in filenames:
            try:
                total += (Path(dirpath) / n).lstat().st_size
                files += 1
            except OSError:
                pass
    return total, files
