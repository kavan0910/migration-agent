import io
import os
import stat
import tarfile
import zipfile

import pytest

from app.safety import ArchiveLimits, UnsafeInput, extract_archive, sanitize_filename, strip_unsafe_tree
from tests.conftest import make_targz, make_zip

L = ArchiveLimits(max_bytes=1024 * 1024, max_file_bytes=512 * 1024, max_files=20, max_ratio=100)


def _w(tmp_path, data, name="a.bin"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_valid_zip_and_tar_extract(tmp_path):
    st = extract_archive(_w(tmp_path, make_zip({"a/b.py": "x", "c.txt": "y"})), tmp_path / "o1", L)
    assert st.files == 2 and (tmp_path / "o1/a/b.py").read_text() == "x"
    st = extract_archive(_w(tmp_path, make_targz({"d/e.py": "z"}), "t.tgz"), tmp_path / "o2", L)
    assert st.files == 1 and (tmp_path / "o2/d/e.py").exists()


def test_validate_only_writes_nothing(tmp_path):
    extract_archive(_w(tmp_path, make_zip()), None, L)
    assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]


@pytest.mark.parametrize("name", ["../evil.txt", "a/../../evil.txt", "/etc/passwd", "..\\evil.txt", "C:\\win\\x", "C:/x",
                                  "a/\x00b", "x/" * 70 + "f"])
def test_zip_slip_variants(tmp_path, name):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(zipfile.ZipInfo(name), "pwn")
    with pytest.raises(UnsafeInput):
        extract_archive(_w(tmp_path, z.getvalue()), tmp_path / "o", L)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path.parent / "evil.txt").exists()


def test_tar_slip_and_links(tmp_path):
    for kind, mk in (("slip", lambda ti: setattr(ti, "name", "../../evil")),
                     ("abs", lambda ti: setattr(ti, "name", "/tmp/evil")),
                     ("sym", lambda ti: (setattr(ti, "type", tarfile.SYMTYPE), setattr(ti, "linkname", "/etc/passwd"))),
                     ("hard", lambda ti: (setattr(ti, "type", tarfile.LNKTYPE), setattr(ti, "linkname", "x"))),
                     ("dev", lambda ti: setattr(ti, "type", tarfile.CHRTYPE)),
                     ("fifo", lambda ti: setattr(ti, "type", tarfile.FIFOTYPE))):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            ti = tarfile.TarInfo("ok.txt")
            mk(ti)
            tf.addfile(ti, io.BytesIO(b"") if ti.type in (tarfile.REGTYPE, tarfile.AREGTYPE) else None)
        with pytest.raises(UnsafeInput):
            extract_archive(_w(tmp_path, buf.getvalue(), f"{kind}.tgz"), tmp_path / f"o-{kind}", L)


def test_zip_symlink_entry_rejected(tmp_path):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zi = zipfile.ZipInfo("link")
        zi.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(zi, "/etc/passwd")
    with pytest.raises(UnsafeInput, match="links"):
        extract_archive(_w(tmp_path, z.getvalue()), tmp_path / "o", L)


def test_zip_bomb_by_total_size_and_ratio(tmp_path):
    bomb = make_zip({f"f{i}.txt": b"0" * 400_000 for i in range(5)})  # 2 MB inflated from a few KB
    assert len(bomb) < 20_000
    with pytest.raises(UnsafeInput):
        extract_archive(_w(tmp_path, bomb), tmp_path / "o", L)
    big = ArchiveLimits(max_bytes=10**9, max_file_bytes=10**9, max_files=10, max_ratio=100)
    z = _w(tmp_path, make_zip({"big.txt": b"0" * (30 * 1024 * 1024)}), "ratio.zip")
    with pytest.raises(UnsafeInput, match="ratio"):
        extract_archive(z, tmp_path / "o2", big)


def test_lying_headers_do_not_bypass_caps(tmp_path):
    """Declared sizes are never trusted: the real stream is counted."""
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", b"0" * 600_000)
    data = bytearray(z.getvalue())
    # corrupt the declared uncompressed size in local + central headers to a tiny value
    for sig in (b"PK\x03\x04", b"PK\x01\x02"):
        i = data.find(sig)
        off = i + (22 if sig == b"PK\x03\x04" else 24)
        data[off:off + 4] = (10).to_bytes(4, "little")
    with pytest.raises(UnsafeInput):
        extract_archive(_w(tmp_path, bytes(data)), tmp_path / "o", L)


def test_too_many_files_and_single_file_cap_and_empty(tmp_path):
    with pytest.raises(UnsafeInput, match="too many"):
        extract_archive(_w(tmp_path, make_zip({f"f{i}": "x" for i in range(30)})), None, L)
    with pytest.raises(UnsafeInput, match="too large"):
        extract_archive(_w(tmp_path, make_targz({"x": os.urandom(600_000)}), "t.tgz"), None, L)
    with pytest.raises(UnsafeInput, match="empty"):
        extract_archive(_w(tmp_path, b"PK\x05\x06" + b"\x00" * 18), None, L)


def test_duplicates_corrupt_and_wrong_type(tmp_path):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "1")
        zf.writestr("A.txt", "2")
    with pytest.raises(UnsafeInput, match="duplicate"):
        extract_archive(_w(tmp_path, z.getvalue()), None, L)
    with pytest.raises(UnsafeInput):
        extract_archive(_w(tmp_path, make_zip()[:40]), None, L)
    with pytest.raises(UnsafeInput, match="unsupported"):
        extract_archive(_w(tmp_path, b"just text"), None, L)
    with pytest.raises(UnsafeInput):
        extract_archive(_w(tmp_path, b"\x1f\x8bgarbage"), None, L)


def test_encrypted_zip_rejected(tmp_path):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "1")
    data = bytearray(z.getvalue())
    data[6] |= 1  # local header flag
    data[data.find(b"PK\x01\x02") + 8] |= 1
    with pytest.raises(UnsafeInput):
        extract_archive(_w(tmp_path, bytes(data)), None, L)


def test_sanitize_filename():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\x\\spec v1.md") == "spec v1.md"
    assert sanitize_filename("we<ird>*.md") == "we_ird__.md"
    for bad in ("", "..", "...", "/", "a" * 300):
        with pytest.raises(UnsafeInput):
            sanitize_filename(bad)


def test_strip_unsafe_tree(tmp_path):
    (tmp_path / "ok.txt").write_text("x")
    os.symlink("/etc/passwd", tmp_path / "l")
    os.symlink(tmp_path, tmp_path / "dirlink")
    assert strip_unsafe_tree(tmp_path) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["ok.txt"]


# ----------------------------------------------------------------------------- through the API
def test_api_upload_validation(alice):
    pid = alice.project(with_source=False)
    url = f"/v1/projects/{pid}/source"
    r = alice.post(url, files={"file": ("a.zip", make_zip({"../x": "1"}), "application/zip")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_source"
    assert alice.post(url, files={"file": ("a.txt", b"plain", "text/plain")}).status_code == 422
    assert alice.post(url, data={"x": "1"}).status_code == 422
    assert alice.post(url, content=b"abc", headers={"content-type": "text/plain"}).status_code == 422
    r = alice.post(url, files={"file": ("a.tgz", make_targz({"s/a.py": "x"}), "application/gzip")})
    assert r.status_code == 201 and r.json()["file_count"] == 1 and r.json()["kind"] == "upload"
    # replacing a source removes the previous object
    r2 = alice.post(url, files={"file": ("b.zip", make_zip(), "application/zip")})
    assert r2.status_code == 201 and alice.get(url).json()["id"] == r2.json()["id"]


def test_api_oversize_upload_is_413(alice, settings):
    pid = alice.project(with_source=False)
    big = os.urandom(6 * 1024 * 1024)  # cap is 5 MB in the fixture
    r = alice.post(f"/v1/projects/{pid}/source", files={"file": ("a.zip", big, "application/zip")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    r = alice.post(f"/v1/projects/{pid}/user-docs", files={"file": ("a.md", b"x" * (2 * 1024 * 1024), "text/markdown")})
    assert r.status_code == 413


def test_api_json_body_cap(alice):
    r = alice.post("/v1/workspaces", content=b'{"name":"' + b"a" * (2 * 1024 * 1024) + b'"}',
                   headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_api_bomb_rejected_at_upload(alice):
    pid = alice.project(with_source=False)
    bomb = make_zip({f"f{i}.txt": b"0" * 8_000_000 for i in range(4)})  # > 20 MB extracted cap
    r = alice.post(f"/v1/projects/{pid}/source", files={"file": ("bomb.zip", bomb, "application/zip")})
    assert r.status_code == 422


def test_user_docs_crud_and_safety(alice, store):
    pid = alice.project()
    u = f"/v1/projects/{pid}/user-docs"
    r = alice.post(u, files={"file": ("../../evil/spec.md", b"# s", "text/markdown")})
    assert r.status_code == 201 and r.json()["filename"] == "spec.md"
    assert alice.post(u, files={"file": ("spec.md", b"again", "text/markdown")}).status_code == 409
    assert alice.post(u, files={"file": ("run.exe", b"MZ", "application/octet-stream")}).status_code == 422
    assert alice.post(u, files={"file": ("empty.md", b"", "text/markdown")}).status_code == 422
    assert alice.get(u).json()["total"] == 1
    did = r.json()["id"]
    assert alice.delete(f"{u}/{did}").status_code == 204
    assert alice.delete(f"{u}/{did}").status_code == 404
    assert alice.get(u).json()["total"] == 0
    assert not any("user_docs" in k for k in store.list(""))
