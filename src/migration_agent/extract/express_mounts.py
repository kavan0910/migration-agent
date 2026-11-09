"""Resolve Express router mount prefixes across files: `app.use('/api', routes)` + `router.use('/orders', orders)`
turn router-relative route paths ("GET /:id") into full paths ("GET /api/orders/:id").

Static and conservative: a file's prefix is applied only when exactly one mount chain reaches it (ambiguous
multi-mount routers are left relative rather than guessed)."""
from __future__ import annotations

import posixpath
import re

from .deps import _resolve_js

REQUIRE_RE = re.compile(r"""(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['"](\.{1,2}/?[^'"]*)['"]\s*\)""")
IMPORT_RE = re.compile(r"""import\s+(\w+)\s+from\s*['"](\.{1,2}/?[^'"]*)['"]""")
USE_RE = re.compile(r"""\b\w+\.use\(\s*['"]([^'"]*)['"]\s*,\s*(?:[\w.()\[\]]+\s*,\s*)*?(\w+)\s*\)""")


def _join(a: str, b: str) -> str:
    out = "/" + "/".join(x for x in (a.strip("/"), b.strip("/")) if x)
    return out


def mount_prefixes(texts: dict[str, str]) -> dict[str, str]:
    """texts: {js file: source}. Returns {file: prefix} for files reached by exactly one mount chain."""
    files = set(texts)
    parents: dict[str, list[tuple[str, str]]] = {}      # child file -> [(parent file, mount path)]
    for f, text in texts.items():
        vars_: dict[str, str] = {}
        for rx in (REQUIRE_RE, IMPORT_RE):
            for m in rx.finditer(text):
                dst = _resolve_js(f, m.group(2), files)
                if dst:
                    vars_[m.group(1)] = dst
        for m in USE_RE.finditer(text):
            child = vars_.get(m.group(2))
            if child and child != f:
                parents.setdefault(child, []).append((f, m.group(1)))

    cache: dict[str, str | None] = {}

    def prefix(f: str, seen: frozenset = frozenset()) -> str | None:
        if f in cache:
            return cache[f]
        ps = parents.get(f)
        if not ps:
            return ""
        if len(ps) != 1 or f in seen:
            return None                                  # ambiguous or cyclic: do not guess
        parent, path = ps[0]
        pp = prefix(parent, seen | {f})
        res = None if pp is None else _join(pp, path)
        cache[f] = res
        return res

    return {f: p for f in parents if (p := prefix(f)) is not None and p}


def apply_prefix(name: str, prefix: str) -> str:
    """"GET /x" + "/api" -> "GET /api/x" (name has the "METHOD path" form)."""
    method, _, path = name.partition(" ")
    if not path.startswith("/") or path.startswith(prefix + "/") or path == prefix:
        return name
    return f"{method} {_join(prefix, path)}" if path != "/" else f"{method} {prefix}"
