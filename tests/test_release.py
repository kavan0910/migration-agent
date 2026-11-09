import hashlib
import importlib.util
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize("tag,ver", [("v0.2.0", "0.2.0"), ("v10.0.12", "10.0.12"),
                                     ("v1.0.0-rc1", "1.0.0rc1"), ("v1.0.0-rc.2", "1.0.0rc2"),
                                     ("v1.0.0-beta1", "1.0.0b1")])
def test_parse_tag(tag, ver):
    assert release.parse_tag(tag) == ver


@pytest.mark.parametrize("tag", ["0.2.0", "v1.2", "v1.2.3.4", "release-1", "v1.2.3-foo", "v1.2.3 ", ""])
def test_bad_tags(tag):
    with pytest.raises(release.ReleaseError):
        release.parse_tag(tag)


def test_tag_must_match_pyproject():
    text = '[project]\nname="x"\nversion = "0.2.0"\n'
    assert release.check_tag_matches("v0.2.0", text) == "0.2.0"
    with pytest.raises(release.ReleaseError, match="0.2.0"):
        release.check_tag_matches("v0.3.0", text)
    with pytest.raises(release.ReleaseError):
        release.read_version("[project]\n")


def test_pyproject_version_is_release_parsable():
    v = release.read_version((ROOT / "pyproject.toml").read_text())
    assert release.parse_tag("v" + v) == v


def test_clean_check():
    release.check_clean("")
    release.check_clean("\n")
    with pytest.raises(release.ReleaseError, match="not clean"):
        release.check_clean(" M src/x.py\n?? new.txt\n")


def test_sha256sums_format(tmp_path):
    f = tmp_path / "a.whl"
    f.write_bytes(b"hello")
    d = release.sha256_file(f)
    assert d == hashlib.sha256(b"hello").hexdigest()
    assert release.format_sha256sums({"b.tar.gz": "2" * 64, "a.whl": d}) == f"{d}  a.whl\n{'2' * 64}  b.tar.gz\n"


def test_cosign_commands_are_printed_not_run():
    cmds = release.cosign_commands(["a.whl", "b.tar.gz"])
    assert cmds[0].startswith("cosign sign-blob --yes --bundle dist/a.whl.sigstore.json dist/a.whl")
    assert "subprocess" not in Path(release.__file__).read_text().split("def cosign_commands")[1].split("def _git")[0]


def test_version_attribute():
    import migration_agent
    assert migration_agent.__version__


@pytest.mark.skipif(shutil.which("git") is None, reason="needs build backend + network")
def test_wheel_includes_prompts_and_templates(tmp_path):
    pytest.importorskip("build")
    r = subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path), str(ROOT)],
                       capture_output=True, text=True)
    if r.returncode:
        pytest.skip("wheel build unavailable offline: " + r.stderr[-200:])
    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()
    for needle in ("analyze/prompts/chunk_extract.system.md", "verify/prompts/compare.user.md",
                   "templates/understanding.md.j2", "templates/changelog.md.j2"):
        assert any(n.endswith(needle) for n in names), needle
