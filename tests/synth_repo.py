"""Synthetic large-repo generator for scale/incremental tests (mixed py/js/java, deep import chains,
~10% random cross-package links)."""
from __future__ import annotations

import random
from pathlib import Path

LANGS = ("py", "js", "java")


def _body_py(pkg: str, k: int, imports: list[str]) -> str:
    lines = [f'"""module {pkg} m{k}"""', *imports, "", f"class Svc{k}:", f"    def run(self, x):"]
    lines += [f"        x = x + {i}  # step {i}" for i in range(12)]
    lines += ["        return x", "", f"def f{k}(a):", f"    return Svc{k}().run(a)", ""]
    return "\n".join(lines)


def _body_js(k: int, imports: list[str]) -> str:
    lines = [*imports, "", f"function f{k}(a) {{"]
    lines += [f"  a = a + {i}; // step {i}" for i in range(12)]
    lines += ["  return a;", "}", f"module.exports = {{ f{k} }};", ""]
    return "\n".join(lines)


def _body_java(pkg: str, k: int, imports: list[str]) -> str:
    lines = [f"package {pkg};", *imports, "", f"public class C{k} {{", "  public int run(int x) {"]
    lines += [f"    x = x + {i}; // step {i}" for i in range(12)]
    lines += ["    return x;", "  }", "}", ""]
    return "\n".join(lines)


def generate_repo(root: Path, n_files: int = 2400, *, per_pkg: int = 40, cross: float = 0.10, seed: int = 7) -> list[str]:
    """Write ~n_files source files under root; return relative paths. Each file imports its predecessor in the
    same package (deep chains of length per_pkg); `cross` of files also import a random file from another package."""
    rng = random.Random(seed)
    root = Path(root)
    n_pkgs = max(1, n_files // per_pkg)
    layout: list[tuple[str, str, int]] = []  # (lang, pkgdir, k)
    for p in range(n_pkgs):
        lang = LANGS[p % 3]
        for k in range(per_pkg):
            layout.append((lang, f"svc{p // 8}/pkg{p}", k))
    paths: dict[tuple[str, int], str] = {}
    out: list[str] = []
    for lang, d, k in layout:
        ext = {"py": "py", "js": "js", "java": "java"}[lang]
        name = f"C{k}" if lang == "java" else f"m{k}"
        paths[(d, k)] = f"{d}/{name}.{ext}"
    pkg_lang = {d: lang for lang, d, _ in layout}
    for lang, d, k in layout:
        imports: list[str] = []
        targets = [(d, k - 1)] if k > 0 else []
        if rng.random() < cross:
            od = rng.choice([x for x in pkg_lang if pkg_lang[x] == lang and x != d] or [d])
            if od != d:
                targets.append((od, rng.randrange(per_pkg)))
        for td, tk in targets:
            if lang == "py":
                if td == d:
                    imports.append(f"from .m{tk} import f{tk}")
                else:
                    imports.append(f"from {td.replace('/', '.')}.m{tk} import f{tk}")
            elif lang == "js":
                rel = f"./m{tk}" if td == d else "../" * (d.count("/") + 1) + f"{td}/m{tk}"
                imports.append(f"const d{tk} = require('{rel}');")
            else:
                imports.append(f"import {td.replace('/', '.')}.C{tk};")
        pkg = d.replace("/", ".")
        body = (_body_py(pkg, k, imports) if lang == "py" else _body_js(k, imports) if lang == "js"
                else _body_java(pkg, k, imports))
        rel = paths[(d, k)]
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
        out.append(rel)
        if lang == "py" and k == 0:
            (f.parent / "__init__.py").write_text("")
    return out


def make_project(tmp: Path, n_files: int, name: str = "big", **kw):
    """init + ingest + extract a synthetic repo; returns (Project, source_dir)."""
    from migration_agent.pipeline import run_extract, run_ingest
    from migration_agent.project import Project
    src = Path(tmp) / f"{name}-src"
    generate_repo(src, n_files, **kw)
    proj = Project.init(Path(tmp) / f"{name}.migration", str(src), name)
    run_ingest(proj, str(src))
    run_extract(proj)
    return proj, src
