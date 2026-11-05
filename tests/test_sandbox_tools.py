import os

import pytest

from migration_agent.sandbox.tools import ReadOnlyTools, SandboxViolation


@pytest.fixture
def jail(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("hello\nworld\nneedle here\n")
    (root / "sub" / "b.py").write_text("x = 1\nneedle = 2\n")
    (root / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET")
    os.symlink(outside / "secret.txt", root / "link_file")
    os.symlink(outside, root / "link_dir")
    os.symlink(root / "a.txt", root / "inner_link")
    return ReadOnlyTools(root), tmp_path


def test_read_and_range(jail):
    t, _ = jail
    r = t.read_file("a.txt")
    assert r.content.startswith("hello") and r.total_lines == 3
    r = t.read_file("a.txt", start=2, end=2)
    assert r.content == "world"


def test_list_and_stat(jail):
    t, _ = jail
    names = {e.name: e.kind for e in t.list_dir().entries}
    assert names["a.txt"] == "file" and names["sub"] == "dir"
    assert names["link_file"] == "symlink-escaping"
    assert t.file_stat("bin.dat").is_binary is True
    assert t.file_stat("a.txt").is_binary is False


def test_grep_skips_escaping_and_binary(jail):
    t, _ = jail
    res = t.grep("needle")
    assert {m.path for m in res.matches} == {"a.txt", "sub/b.py"}
    assert not t.grep("SECRET").matches


@pytest.mark.parametrize("p", ["../outside/secret.txt", "sub/../../outside/secret.txt", "link_file",
                               "link_dir/secret.txt", "/etc/passwd", "a\x00.txt"])
def test_escapes_rejected(jail, p):
    t, _ = jail
    with pytest.raises(SandboxViolation):
        t.read_file(p)


def test_absolute_outside_and_inside(jail):
    t, tmp = jail
    with pytest.raises(SandboxViolation):
        t.file_stat(str(tmp / "outside" / "secret.txt"))
    assert t.read_file(str(t.root / "a.txt")).total_lines == 3
    with pytest.raises(SandboxViolation):
        t.list_dir("link_dir")
    with pytest.raises(SandboxViolation):
        t.grep("x", "..")


def test_binary_and_size_caps(jail):
    t, _ = jail
    with pytest.raises(SandboxViolation, match="binary"):
        t.read_file("bin.dat")
    (t.root / "big.txt").write_text("a\n" * (ReadOnlyTools.MAX_FILE_BYTES // 2 + 10))
    with pytest.raises(SandboxViolation, match="size"):
        t.read_file("big.txt")
    (t.root / "many.txt").write_text("l\n" * 5000)
    r = t.read_file("many.txt")
    assert r.truncated and r.end == ReadOnlyTools.MAX_READ_LINES


def test_no_mutating_api():
    banned = ("write", "delete", "remove", "exec", "run", "open", "connect", "fetch", "request", "post")
    public = [n for n in dir(ReadOnlyTools) if not n.startswith("_") and not n.isupper()]
    assert sorted(public) == ["file_stat", "grep", "list_dir", "read_file", "root"]
    assert not any(b in n for n in public for b in banned)
