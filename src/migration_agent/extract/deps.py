"""Static file-to-file dependency facts (imports/requires/same-package type use), resolved inside the repo.

Returns [(src_file, dst_file, line)] deterministically sorted. Only references that resolve to a file in the
repo are returned (third-party imports are ignored here; manifests cover those)."""
from __future__ import annotations

import posixpath
import re
from pathlib import Path

JS_RES = [re.compile(r"""require\(\s*['"](\.{1,2}/?[^'"]*)['"]\s*\)"""),
          re.compile(r"""(?:import|export)\b[^'"\n]*?from\s*['"](\.{1,2}/?[^'"]*)['"]"""),
          re.compile(r"""^\s*import\s*['"](\.{1,2}/?[^'"]*)['"]""")]
JS_EXT = ("", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", "/index.js", "/index.ts")
PY_FROM = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+(.+)$")
PY_IMPORT = re.compile(r"^\s*import\s+([\w.]+)")
JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;")
JAVA_PKG = re.compile(r"^\s*package\s+([\w.]+)\s*;")


def _resolve_js(src: str, spec: str, files: set[str]) -> str | None:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(src), spec))
    for ext in JS_EXT:
        if base + ext in files:
            return base + ext
    return None


def _py_candidates(mod_path: str, files: set[str]) -> str | None:
    for c in (mod_path + ".py", mod_path + "/__init__.py"):
        if c in files:
            return c
    return None


def _python(src: str, lines: list[str], files: set[str]):
    out = []
    pkg_dir = posixpath.dirname(src)
    for i, line in enumerate(lines, 1):
        m = PY_FROM.match(line)
        if m:
            dots, mod, names = m.groups()
            if dots:
                base = pkg_dir
                for _ in range(len(dots) - 1):
                    base = posixpath.dirname(base)
                mod_path = posixpath.join(base, *mod.split(".")) if mod else base
            else:
                mod_path = "/".join(mod.split("."))
            hit = _py_candidates(mod_path, files) if mod else None
            if hit:
                out.append((src, hit, i))
            for n in (x.strip().split(" as ")[0].strip("() ") for x in names.split(",")):
                sub = _py_candidates(posixpath.join(mod_path, n), files) if n.isidentifier() else None
                if sub:
                    out.append((src, sub, i))
            continue
        m = PY_IMPORT.match(line)
        if m:
            hit = _py_candidates(m.group(1).replace(".", "/"), files)
            if hit:
                out.append((src, hit, i))
    return out


def _java(src: str, lines: list[str], files: set[str], by_class: dict[str, list[str]]):
    out, seen = [], set()
    text_pkg = next((m.group(1) for ln in lines if (m := JAVA_PKG.match(ln))), "")
    for i, line in enumerate(lines, 1):
        m = JAVA_IMPORT.match(line)
        if m:
            cand = [f for f in by_class.get(m.group(1).rsplit(".", 1)[-1], []) if f != src]
            for c in cand:
                if c.replace("/", ".").removesuffix(".java").endswith(m.group(1)):
                    out.append((src, c, i))
                    seen.add(c)
    stripped = [re.sub(r"//.*|\"[^\"]*\"", "", ln) for ln in lines]
    for cls, paths in by_class.items():
        for dst in paths:
            if dst == src or dst in seen or posixpath.dirname(dst) != posixpath.dirname(src):
                continue
            rx = re.compile(r"\b" + re.escape(cls) + r"\b")
            for i, ln in enumerate(stripped, 1):
                if rx.search(ln) and not JAVA_PKG.match(ln) and not ln.lstrip().startswith("import"):
                    out.append((src, dst, i))
                    break
    return out


def extract_deps(code_dir: Path, files: list[str]) -> list[tuple[str, str, int]]:
    fileset = set(files)
    by_class: dict[str, list[str]] = {}
    for f in files:
        if f.endswith(".java"):
            by_class.setdefault(posixpath.basename(f)[:-5], []).append(f)
    out: set[tuple[str, str, int]] = set()
    for f in sorted(files):
        ext = posixpath.splitext(f)[1]
        if ext not in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".java"):
            continue
        try:
            lines = (Path(code_dir) / f).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        if ext == ".py":
            out.update(_python(f, lines, fileset))
        elif ext == ".java":
            out.update(_java(f, lines, fileset, by_class))
        else:
            for i, line in enumerate(lines, 1):
                for rx in JS_RES:
                    for m in rx.finditer(line):
                        dst = _resolve_js(f, m.group(1), fileset)
                        if dst and dst != f:
                            out.add((f, dst, i))
    first: dict[tuple[str, str], int] = {}
    for s, d, ln in sorted(out):
        first.setdefault((s, d), ln)
    return [(s, d, ln) for (s, d), ln in sorted(first.items()) if s != d]
