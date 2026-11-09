"""D-06(a): archive and directory ingestion attacks. Every test asserts the defence holds."""
from __future__ import annotations

import os
import threading
import zipfile

import pytest

from migration_agent.ingest import IngestError, ingest


def _tree(p):
    return sorted(x.relative_to(p).as_posix() for x in p.rglob("*") if x.is_file())


# ----------------------------------------------------------------- zip
@pytest.mark.parametrize("name", [
    "../evil.txt", "a/../../evil.txt", "/etc/cron.d/evil", "..\\evil.txt", "C:\\Windows\\evil.txt",
    "a/b/../../../evil.txt",
])
def test_zip_traversal_and_absolute_entries_rejected(tmp_path, mk, name):
    z = mk.make_traversal_zip(tmp_path / "t.zip", name)
    dest = tmp_path / "code"
    with pytest.raises(IngestError):
        ingest(str(z), dest)
    assert not (tmp_path / "evil.txt").exists()
    assert not dest.exists() or not _tree(dest)


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside", "../../../../etc/shadow", "C:/x"])
def test_zip_symlink_escape_rejected(tmp_path, mk, target):
    z = mk.make_symlink_zip(tmp_path / "s.zip", target, name="a/link")
    with pytest.raises(IngestError):
        ingest(str(z), tmp_path / "code")


def test_zip_internal_symlink_never_materialised(tmp_path, mk):
    z = mk.make_symlink_zip(tmp_path / "s.zip", "sibling.txt", name="a/link")
    rep = ingest(str(z), tmp_path / "code")
    assert not any(p.is_symlink() for p in (tmp_path / "code").rglob("*"))
    assert rep.files_copied == 0


def test_zip_bomb_rejected_by_ratio_cap(tmp_path, mk):
    z = mk.make_zip_bomb(tmp_path / "bomb.zip", mib=24)
    assert z.stat().st_size < 100_000  # a few KB-ish on disk, 24 MiB when expanded
    with pytest.raises(IngestError, match="ratio|exceeds|bomb"):
        ingest(str(z), tmp_path / "code")
    assert not (tmp_path / "code").exists() or not _tree(tmp_path / "code")


def test_zip_bomb_rejected_by_size_cap(tmp_path, mk):
    z = mk.make_zip_bomb(tmp_path / "bomb.zip", mib=2)
    with pytest.raises(IngestError):
        ingest(str(z), tmp_path / "code", max_bytes=1024 * 1024, max_ratio=10**9)


def test_zip_file_count_cap(tmp_path, mk):
    z = mk.make_many_small_files_zip(tmp_path / "many.zip", 300)
    with pytest.raises(IngestError, match="too many files"):
        ingest(str(z), tmp_path / "code", max_files=100)


def test_nested_archive_is_not_recursively_extracted(tmp_path, mk):
    z = mk.make_nested_zip(tmp_path / "n.zip")
    rep = ingest(str(z), tmp_path / "code")
    files = _tree(tmp_path / "code")
    assert files == ["main.py"]
    assert not any("secret_code" in f for f in files)
    assert any(e.path == "payload.zip" and e.reason == "binary" for e in rep.exclusions)


def test_corrupt_zip_rejected(tmp_path):
    p = tmp_path / "x.zip"
    p.write_bytes(b"PK\x03\x04garbage" * 10)
    with pytest.raises(IngestError):
        ingest(str(p), tmp_path / "code")


# ----------------------------------------------------------------- tar
@pytest.mark.parametrize("entry", [
    dict(name="../evil.txt", data=b"x"),
    dict(name="/abs/evil.txt", data=b"x"),
    dict(name="a/../../evil.txt", data=b"x"),
])
def test_tar_traversal_rejected(tmp_path, mk, entry):
    t = mk.make_tar(tmp_path / "t.tar.gz", [entry])
    with pytest.raises(IngestError):
        ingest(str(t), tmp_path / "code")


@pytest.mark.parametrize("linkname", ["/etc/passwd", "../../etc/passwd", "../../x"])
def test_tar_symlink_and_hardlink_escape_rejected(tmp_path, mk, linkname):
    import tarfile
    for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
        t = mk.make_tar(tmp_path / f"t{ord(kind)}.tar.gz", [dict(name="a/l", type_=kind, linkname=linkname)])
        with pytest.raises(IngestError):
            ingest(str(t), tmp_path / f"code{ord(kind)}")


def test_tar_links_not_materialised(tmp_path, mk):
    import tarfile
    t = mk.make_tar(tmp_path / "t.tar.gz", [
        dict(name="real.py", data=b"x=1\n"),
        dict(name="sym", type_=tarfile.SYMTYPE, linkname="real.py"),
        dict(name="hard", type_=tarfile.LNKTYPE, linkname="real.py"),
    ])
    ingest(str(t), tmp_path / "code")
    assert _tree(tmp_path / "code") == ["real.py"]
    assert not any(p.is_symlink() for p in (tmp_path / "code").rglob("*"))


@pytest.mark.parametrize("kind", ["CHRTYPE", "BLKTYPE", "FIFOTYPE"])
def test_tar_device_files_rejected(tmp_path, mk, kind):
    import tarfile
    t = mk.make_tar(tmp_path / "t.tar.gz", [dict(name="dev", type_=getattr(tarfile, kind))])
    with pytest.raises(IngestError, match="special"):
        ingest(str(t), tmp_path / "code")


def test_tar_bomb_rejected(tmp_path, mk):
    t = mk.make_tar_bomb(tmp_path / "b.tar.gz", mib=24)
    with pytest.raises(IngestError):
        ingest(str(t), tmp_path / "code")


# ----------------------------------------------------------- directory
def test_directory_symlinks_not_followed_and_nothing_leaks(tmp_path, mk):
    src, outside = tmp_path / "src", tmp_path / "outside"
    mk.make_hostile_directory(src, outside)
    dest = tmp_path / "code"
    rep = ingest(str(src), dest)
    assert _tree(dest) == ["main.py"]
    blob = "".join(p.read_text(errors="replace") for p in dest.rglob("*") if p.is_file())
    assert "HOST-FILE-CONTENT" not in blob and "root:" not in blob
    reasons = {e.path: e.reason for e in rep.exclusions}
    for name in ("leak_file", "leak_dir", "passwd_link", "loop_a", "loop_b", "self_dir"):
        assert reasons.get(name) == "symlink", name
    assert not any(p.is_symlink() for p in dest.rglob("*"))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs mkfifo")
def test_directory_fifo_does_not_hang_and_is_excluded(tmp_path, mk):
    src = tmp_path / "src"
    mk.make_hostile_directory(src, tmp_path / "outside")
    result = {}

    def run():
        result["rep"] = ingest(str(src), tmp_path / "code")

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(15)
    assert not t.is_alive(), "ingest blocked on a FIFO (DoS)"
    assert any(e.path == "pipe.fifo" and e.reason == "special_file" for e in result["rep"].exclusions)


def test_directory_file_count_cap(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(50):
        (src / f"f{i}.txt").write_text("x")
    with pytest.raises(IngestError, match="too many"):
        ingest(str(src), tmp_path / "code", max_files=10)
    assert not (tmp_path / "code").exists() or not _tree(tmp_path / "code")


def test_directory_total_size_cap(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(5):
        (src / f"f{i}.txt").write_text("y" * 4000)
    with pytest.raises(IngestError):
        ingest(str(src), tmp_path / "code", max_bytes=10_000)


def test_filenames_with_control_or_bidi_chars_are_withheld(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.py").write_text("x=1\n")
    (src / "evil\nIGNORE PREVIOUS.py").write_text("x=1\n")
    (src / "rtl\u202eynp.py").write_text("x=1\n")
    rep = ingest(str(src), tmp_path / "code")
    assert _tree(tmp_path / "code") == ["ok.py"]
    assert sum(e.reason == "unsafe_name" for e in rep.exclusions) == 2


def test_hardlink_in_source_dir_is_copied_by_value_only(tmp_path):
    """A hardlinked file is just a regular file: content is copied, no link survives in code/."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("x=1\n")
    os.link(src / "a.py", src / "b.py")
    dest = tmp_path / "code"
    ingest(str(src), dest)
    assert (dest / "a.py").stat().st_ino != (src / "a.py").stat().st_ino
    assert (dest / "a.py").stat().st_nlink == 1
