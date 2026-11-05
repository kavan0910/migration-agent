"""Parse build/dependency manifests into structured facts (A-04)."""

from __future__ import annotations

import json
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, Field

SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "dist", ".venv", "venv", "vendor",
    "__pycache__", ".gradle", ".idea", "bin", "obj", ".tox",
}
MANIFEST_NAMES = {
    "pom.xml", "package.json", "go.mod", "Cargo.toml", "pyproject.toml", "build.gradle",
    "build.gradle.kts",
}


class Dependency(BaseModel):
    name: str
    version: str | None = None
    scope: str | None = None


class Manifest(BaseModel):
    path: str
    ecosystem: str
    name: str | None = None
    version: str | None = None
    runtime: dict[str, str] = Field(default_factory=dict)
    dependencies: list[Dependency] = Field(default_factory=list)
    parse_error: str | None = None


class ManifestFacts(BaseModel):
    manifests: list[Manifest] = Field(default_factory=list)

    @property
    def errors(self) -> list[Manifest]:
        return [m for m in self.manifests if m.parse_error]


# ---------------------------------------------------------------- helpers

def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _text(el: ET.Element | None, tag: str) -> str | None:
    if el is None:
        return None
    c = el.find(tag)
    return c.text.strip() if c is not None and c.text and c.text.strip() else None


def _sort_deps(deps: list[Dependency]) -> list[Dependency]:
    return sorted(deps, key=lambda d: (d.name, d.scope or "", d.version or ""))


# ---------------------------------------------------------------- parsers

def _parse_pom(text: str, m: Manifest) -> None:
    root = _strip_ns(ET.fromstring(text))
    props = {}
    pe = root.find("properties")
    if pe is not None:
        for c in pe:
            props[c.tag] = (c.text or "").strip()
    parent = root.find("parent")
    gid = _text(root, "groupId") or _text(parent, "groupId")
    aid = _text(root, "artifactId")
    props.update({"project.version": _text(root, "version") or _text(parent, "version") or "",
                  "project.groupId": gid or ""})

    def sub(v: str | None) -> str | None:
        if v is None:
            return None
        return re.sub(r"\$\{([^}]+)\}", lambda mo: props.get(mo.group(1), mo.group(0)), v)

    m.name = f"{gid}:{aid}" if gid and aid else aid
    m.version = sub(_text(root, "version") or _text(parent, "version"))
    for key in ("java.version", "maven.compiler.source", "maven.compiler.target",
                "maven.compiler.release"):
        if props.get(key):
            m.runtime[key] = props[key]
    if parent is not None:
        pv = f"{_text(parent, 'groupId')}:{_text(parent, 'artifactId')}:{_text(parent, 'version')}"
        m.runtime["parent"] = pv
    for plugin in root.iter("plugin"):
        if _text(plugin, "artifactId") == "maven-compiler-plugin":
            cfg = plugin.find("configuration")
            for k in ("source", "target", "release"):
                v = sub(_text(cfg, k))
                if v:
                    m.runtime.setdefault(f"maven.compiler.{k}", v)
    deps_parent = root.find("dependencies")
    if deps_parent is not None:
        for d in deps_parent.findall("dependency"):
            g, a = _text(d, "groupId"), _text(d, "artifactId")
            m.dependencies.append(Dependency(
                name=f"{g}:{a}", version=sub(_text(d, "version")),
                scope=_text(d, "scope") or "compile"))


def _parse_package_json(text: str, m: Manifest) -> None:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("package.json root is not an object")
    m.name, m.version = data.get("name"), data.get("version")
    for k, v in (data.get("engines") or {}).items():
        m.runtime[f"engines.{k}"] = str(v)
    for section, scope in (("dependencies", "runtime"), ("devDependencies", "dev"),
                           ("peerDependencies", "peer"), ("optionalDependencies", "optional")):
        for n, v in (data.get(section) or {}).items():
            m.dependencies.append(Dependency(name=n, version=str(v), scope=scope))


def _parse_go_mod(text: str, m: Manifest) -> None:
    in_req = False
    seen_module = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip() if not raw.strip().startswith("//") else ""
        indirect = "// indirect" in raw
        if not line:
            continue
        if in_req:
            if line == ")":
                in_req = False
                continue
            parts = line.split()
            if len(parts) >= 2:
                m.dependencies.append(Dependency(
                    name=parts[0], version=parts[1], scope="indirect" if indirect else "direct"))
            continue
        if line.startswith("module "):
            m.name = line.split(None, 1)[1].strip().strip('"')
            seen_module = True
        elif line.startswith("go "):
            m.runtime["go"] = line.split()[1]
        elif line.startswith("toolchain "):
            m.runtime["toolchain"] = line.split()[1]
        elif re.match(r"require\s*\(", line):
            in_req = True
        elif line.startswith("require "):
            parts = line.split()
            if len(parts) >= 3:
                m.dependencies.append(Dependency(
                    name=parts[1], version=parts[2], scope="indirect" if indirect else "direct"))
    if not seen_module:
        raise ValueError("go.mod has no module directive")


_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*([<>=!~].*)?$")


def _parse_requirements(text: str, m: Manifest) -> None:
    bad = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-", "git+", "http")):
            continue
        line = line.split(";", 1)[0].strip()
        mo = _REQ_RE.match(line)
        if not mo:
            bad.append(n)
            continue
        m.dependencies.append(Dependency(
            name=mo.group(1), version=(mo.group(3) or "").replace(" ", "") or None, scope="runtime"))
    if bad and not m.dependencies:
        raise ValueError(f"unparseable requirement lines: {bad}")


def _parse_cargo(text: str, m: Manifest) -> None:
    data = tomllib.loads(text)
    pkg = data.get("package") or {}
    m.name, m.version = pkg.get("name"), pkg.get("version") if isinstance(pkg.get("version"), str) else None
    if "edition" in pkg:
        m.runtime["edition"] = str(pkg["edition"])
    if "rust-version" in pkg:
        m.runtime["rust-version"] = str(pkg["rust-version"])
    for section, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev"),
                           ("build-dependencies", "build")):
        for n, v in (data.get(section) or {}).items():
            ver = v if isinstance(v, str) else (v.get("version") if isinstance(v, dict) else None)
            m.dependencies.append(Dependency(name=n, version=ver, scope=scope))


def _parse_pyproject(text: str, m: Manifest) -> None:
    data = tomllib.loads(text)
    proj = data.get("project") or {}
    poetry = (data.get("tool") or {}).get("poetry") or {}
    m.name = proj.get("name") or poetry.get("name")
    m.version = proj.get("version") or poetry.get("version")
    if proj.get("requires-python"):
        m.runtime["requires-python"] = proj["requires-python"]
    for spec in proj.get("dependencies") or []:
        mo = _REQ_RE.match(spec.split(";", 1)[0].strip())
        if mo:
            m.dependencies.append(Dependency(
                name=mo.group(1), version=(mo.group(3) or "").replace(" ", "") or None, scope="runtime"))
    for grp, specs in (proj.get("optional-dependencies") or {}).items():
        for spec in specs:
            mo = _REQ_RE.match(spec.split(";", 1)[0].strip())
            if mo:
                m.dependencies.append(Dependency(
                    name=mo.group(1), version=(mo.group(3) or "").replace(" ", "") or None,
                    scope=f"optional:{grp}"))
    for n, v in (poetry.get("dependencies") or {}).items():
        ver = v if isinstance(v, str) else (v.get("version") if isinstance(v, dict) else None)
        if n == "python":
            m.runtime["python"] = str(ver)
        else:
            m.dependencies.append(Dependency(name=n, version=ver, scope="runtime"))
    for n, v in ((poetry.get("group") or {}).get("dev", {}).get("dependencies") or {}).items():
        ver = v if isinstance(v, str) else (v.get("version") if isinstance(v, dict) else None)
        m.dependencies.append(Dependency(name=n, version=ver, scope="dev"))


_GRADLE_DEP = re.compile(
    r"""^\s*(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|
         annotationProcessor|kapt|compile|testCompile)\s*\(?\s*['"]([^'":]+):([^'":]+)(?::([^'"]+))?['"]""",
    re.X | re.M)


def _parse_gradle(text: str, m: Manifest) -> None:
    if text.count("{") != text.count("}"):
        raise ValueError("unbalanced braces in build.gradle")
    for pat, key in ((r"sourceCompatibility\s*=?\s*['\"]?([\w.]+)", "sourceCompatibility"),
                     (r"targetCompatibility\s*=?\s*['\"]?([\w.]+)", "targetCompatibility"),
                     (r"JavaLanguageVersion\.of\((\d+)\)", "java.toolchain"),
                     (r"jvmTarget\s*=\s*['\"]([\w.]+)", "jvmTarget")):
        mo = re.search(pat, text)
        if mo:
            m.runtime[key] = mo.group(1)
    mo = re.search(r"^\s*version\s*=?\s*['\"]([^'\"]+)['\"]", text, re.M)
    m.version = mo.group(1) if mo else None
    for mo in _GRADLE_DEP.finditer(text):
        conf = mo.group(1)
        scope = "test" if conf.startswith("test") else conf
        m.dependencies.append(Dependency(
            name=f"{mo.group(2)}:{mo.group(3)}", version=mo.group(4), scope=scope))


def _parse_csproj(text: str, m: Manifest) -> None:
    root = _strip_ns(ET.fromstring(text))
    for pg in root.findall("PropertyGroup"):
        for tag in ("TargetFramework", "TargetFrameworks", "LangVersion"):
            v = _text(pg, tag)
            if v:
                m.runtime.setdefault(tag, v)
        m.name = m.name or _text(pg, "AssemblyName") or _text(pg, "PackageId")
        m.version = m.version or _text(pg, "Version")
    for pr in root.iter("PackageReference"):
        n = pr.get("Include") or pr.get("Update")
        if n:
            m.dependencies.append(Dependency(
                name=n, version=pr.get("Version") or _text(pr, "Version"), scope="runtime"))


def _classify(path: Path) -> tuple[str, callable] | None:
    n = path.name
    if n == "pom.xml":
        return "maven", _parse_pom
    if n == "package.json":
        return "npm", _parse_package_json
    if n == "go.mod":
        return "go", _parse_go_mod
    if n == "Cargo.toml":
        return "cargo", _parse_cargo
    if n == "pyproject.toml":
        return "python", _parse_pyproject
    if n in ("build.gradle", "build.gradle.kts"):
        return "gradle", _parse_gradle
    if n.endswith(".csproj"):
        return "nuget", _parse_csproj
    if re.fullmatch(r"requirements[\w.-]*\.txt", n):
        return "pip", _parse_requirements
    return None


# ---------------------------------------------------------------- API

def parse_manifests(code_dir: Path) -> ManifestFacts:
    code_dir = Path(code_dir)
    found: list[Manifest] = []
    for dirpath, dirnames, filenames in os.walk(code_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            cls = _classify(p)
            if cls is None:
                continue
            eco, fn_parse = cls
            m = Manifest(path=p.relative_to(code_dir).as_posix(), ecosystem=eco)
            try:
                fn_parse(p.read_text(encoding="utf-8", errors="replace"), m)
            except Exception as e:  # malformed file: record, never crash
                m.parse_error = f"{type(e).__name__}: {e}"
            m.dependencies = _sort_deps(m.dependencies)
            m.runtime = dict(sorted(m.runtime.items()))
            found.append(m)
    found.sort(key=lambda x: x.path)
    return ManifestFacts(manifests=found)


def write_manifests(facts: ManifestFacts, out: Path) -> Path:
    """Write facts as JSON. `out` may be a facts directory or a file path."""
    out = Path(out)
    if out.suffix != ".json":
        out = out / "manifests.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(facts.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return out
