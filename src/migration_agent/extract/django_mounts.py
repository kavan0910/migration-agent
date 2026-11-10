"""Resolve Django `include()` prefixes across URLconf files.

`path('api/', include('blog.urls'))` in the root urls.py means every route in blog/urls.py lives under `/api/`.
`extract_django` sees one file at a time, so this pass computes {urls file: prefix} from the include() calls and
`apply_django_prefix` rewrites route names. Static and conservative, like `express_mounts`: a file reached by more
than one include chain (or a cycle) is left relative rather than guessed. Only dotted string targets
(`include('pkg.mod')`, `include(('pkg.mod', 'ns'))`) are resolved; `include(router.urls)` is a same-file mount.
"""
from __future__ import annotations

import re

from .frameworks import _DJ_CALL, _django_regex_to_path, _first_str_arg, _strs, balanced, split_top

_INCLUDE = re.compile(r"\binclude\s*\(")


def _blank(text: str) -> str:
    text = re.sub(r'(\"\"\"|\'\'\').*?\1', lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"(?m)#.*$", lambda m: " " * len(m.group(0)), text)


def _resolve(dotted: str, files: set[str]) -> str | None:
    suffix = "/" + dotted.replace(".", "/") + ".py"
    hits = [f for f in files if ("/" + f).endswith(suffix)]
    if len(hits) != 1:
        hits = [f for f in files if ("/" + f).endswith("/" + dotted.replace(".", "/") + "/__init__.py")]
    return hits[0] if len(hits) == 1 else None


def _clean(prefix: str) -> str:
    return "/".join(s for s in prefix.split("/") if s)


def django_mount_prefixes(texts: dict[str, str]) -> dict[str, str]:
    """texts: {urls .py file: source}. Returns {file: '/prefix'} for files reached by exactly one include chain."""
    files = set(texts)
    parents: dict[str, list[tuple[str, str]]] = {}
    for f, raw in texts.items():
        text = _blank(raw)
        for m in _DJ_CALL.finditer(text):
            e = balanced(text, m.end() - 1)
            if e < 0:
                continue
            args = text[m.end():e - 1]
            parts = split_top(args)
            if len(parts) < 2 or not _INCLUDE.match(parts[1].strip()):
                continue
            route = _first_str_arg(args, ("route", "regex"))
            inc = parts[1].strip()
            targets = _strs(inc)
            if route is None or not targets:
                continue
            child = _resolve(targets[0], files)
            if child and child != f:
                pre = route if m.group(1) == "path" else _django_regex_to_path(route)
                parents.setdefault(child, []).append((f, _clean(pre)))

    cache: dict[str, str | None] = {}

    def prefix(f: str, seen: frozenset = frozenset()) -> str | None:
        if f in cache:
            return cache[f]
        ps = parents.get(f)
        if not ps:
            return ""
        if len(ps) != 1 or f in seen:
            return None
        parent, path = ps[0]
        pp = prefix(parent, seen | {f})
        res = None if pp is None else "/".join(x for x in (pp, path) if x)
        cache[f] = res
        return res

    out = {}
    for f in parents:
        p = prefix(f)
        if p:
            out[f] = "/" + p
    return out


def apply_django_prefix(name: str, prefix: str) -> str:
    """"ANY workshops/" + "/api" -> "ANY /api/workshops/" ("METHOD path" form; idempotent)."""
    method, _, path = name.partition(" ")
    if path.startswith(prefix + "/") or path == prefix:
        return name
    rest = path.lstrip("/")
    return f"{method} {prefix}/{rest}" if rest else f"{method} {prefix}"
