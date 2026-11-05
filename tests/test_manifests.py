import json
from pathlib import Path

from migration_agent.extract.manifests import ManifestFacts, parse_manifests, write_manifests

FIX = Path(__file__).parent / "fixtures" / "manifests"


def by_path(facts):
    return {m.path: m for m in facts.manifests}


def test_all_formats_and_determinism():
    facts = parse_manifests(FIX)
    paths = [m.path for m in facts.manifests]
    assert paths == sorted(paths)
    assert facts.model_dump_json() == parse_manifests(FIX).model_dump_json()
    assert {m.ecosystem for m in facts.manifests} >= {
        "maven", "npm", "go", "pip", "python", "cargo", "nuget", "gradle"}


def test_pom():
    m = by_path(parse_manifests(FIX))["java/pom.xml"]
    assert m.name == "com.acme:orders" and m.version == "1.2.3"
    assert m.runtime["java.version"] == "11"
    deps = {d.name: d for d in m.dependencies}
    assert deps["org.junit.jupiter:junit-jupiter"].version == "5.9.0"
    assert deps["org.junit.jupiter:junit-jupiter"].scope == "test"


def test_package_json():
    m = by_path(parse_manifests(FIX))["node/package.json"]
    assert m.runtime["engines.node"] == ">=18"
    assert {(d.name, d.scope) for d in m.dependencies} == {("express", "runtime"), ("jest", "dev")}


def test_go_mod():
    m = by_path(parse_manifests(FIX))["go/go.mod"]
    assert m.name == "github.com/acme/svc" and m.runtime["go"] == "1.21"
    deps = {d.name: d for d in m.dependencies}
    assert deps["golang.org/x/sys"].scope == "indirect"
    assert deps["github.com/lib/pq"].version == "v1.10.9"


def test_requirements_pyproject():
    facts = by_path(parse_manifests(FIX))
    r = facts["py/requirements.txt"]
    assert {(d.name, d.version) for d in r.dependencies} == {
        ("flask", "==2.3.0"), ("requests", ">=2.0,<3")}
    p = facts["py/pyproject.toml"]
    assert p.runtime["requires-python"] == ">=3.10" and p.name == "svc"
    assert {d.name for d in p.dependencies} == {"click", "pydantic"}


def test_cargo_csproj_gradle():
    facts = by_path(parse_manifests(FIX))
    c = facts["rust/Cargo.toml"]
    assert c.runtime["edition"] == "2021"
    assert {(d.name, d.version, d.scope) for d in c.dependencies} >= {
        ("serde", "1.0", "runtime"), ("tempfile", "3", "dev")}
    n = facts["dotnet/App.csproj"]
    assert n.runtime["TargetFramework"] == "net6.0"
    assert n.dependencies[0].name == "Newtonsoft.Json"
    g = facts["gradle/build.gradle"]
    assert g.runtime["sourceCompatibility"] == "17"
    assert {d.name for d in g.dependencies} == {"org.slf4j:slf4j-api", "junit:junit"}


def test_malformed_recorded_not_raised():
    facts = by_path(parse_manifests(FIX))
    for p in ("bad/package.json", "bad/pom.xml", "bad/go.mod", "bad/Cargo.toml"):
        assert facts[p].parse_error, p
    assert facts["node/package.json"].parse_error is None


def test_write_roundtrip(tmp_path):
    facts = parse_manifests(FIX)
    out = write_manifests(facts, tmp_path / "facts")
    assert out.name == "manifests.json"
    assert ManifestFacts.model_validate(json.loads(out.read_text())) == facts


def test_empty_dir_and_skip(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.json").write_text("{}")
    assert parse_manifests(tmp_path).manifests == []
