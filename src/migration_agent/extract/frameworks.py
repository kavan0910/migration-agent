"""D-01 framework route extractors (static, never executes code).

Public API:
    extract_routes(rel_path, text) -> list[Found]   # dispatches on file extension
    extract_spring / extract_python_web / extract_django / extract_rails / extract_aspnet (text, rel) -> list[Found]

Every extractor returns ``Found`` records (kind="route", name="METHOD /full/path", file, line) in
source order; ``name`` uses an uppercase method (``ANY`` when unknown). Prefixes (class-level mappings,
Blueprint/APIRouter prefixes, Rails namespaces, MapGroup) are resolved only when statically visible
in the same file. Malformed input never raises.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any

HTTP = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


@dataclass
class Found:
    kind: str  # route | schema_entity
    name: str
    file: str
    line: int
    attrs: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers
class _Lines:
    def __init__(self, text: str):
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def of(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)


def join_path(*parts: str) -> str:
    segs = []
    for p in parts:
        segs += [s for s in (p or "").split("/") if s]
    return "/" + "/".join(segs)


def balanced(text: str, i: int, open_: str = "(", close: str = ")") -> int:
    """Index just past the bracket matching text[i]==open_ (quote aware); -1 if unbalanced."""
    depth, n, q = 0, len(text), None
    j = i
    while j < n:
        c = text[j]
        if q:
            if c == "\\":
                j += 1
            elif c == q:
                q = None
        elif c in "\"'" or (c == "`"):
            q = c
        elif c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def split_top(s: str) -> list[str]:
    out, depth, q, cur = [], 0, None, []
    i = 0
    while i < len(s):
        c = s[i]
        if q:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                i += 1
                cur.append(s[i])
            elif c == q:
                q = None
        elif c in "\"'":
            q = c
            cur.append(c)
        elif c in "([{":
            depth += 1
            cur.append(c)
        elif c in ")]}":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


_STR = r"""(?:[rbuRBU]{0,2})(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""
_STR_RE = re.compile(_STR)


def _strs(s: str) -> list[str]:
    return [a or b for a, b in _STR_RE.findall(s)]


def _first_str_arg(args: str, keys: tuple[str, ...] = ("path", "rule", "value")) -> str | None:
    parts = split_top(args)
    for k, p in enumerate(parts):
        p = p.strip()
        m = re.match(r"(\w+)\s*=(?!=)\s*(.*)$", p, re.S)
        if m:
            if m.group(1) in keys:
                s = _strs(m.group(2))
                return s[0] if s else None
            continue
        if k == 0:
            m2 = re.fullmatch(_STR, p, re.S)
            if m2:
                return m2.group(1) if m2.group(1) is not None else m2.group(2)
            return None
    return None


def _kw(args: str, key: str) -> str | None:
    for p in split_top(args):
        m = re.match(rf"\s*{key}\s*=(?!=)\s*(.*)$", p, re.S)
        if m:
            return m.group(1).strip()
    return None


def _route(rel: str, line: int, method: str, path: str) -> Found:
    return Found("route", f"{method.upper() if method else 'ANY'} {path}", rel, line)


# ---------------------------------------------------------------- Spring
_SPRING_ANN = re.compile(r"@(Request|Get|Post|Put|Patch|Delete)Mapping\b")
_CLASS_DECL = re.compile(r"(?:^|\n)[ \t]*(?:(?:public|protected|private|abstract|final|static)\s+)*(?:class|interface)\s+\w+")


def _skip_annotations(text: str, i: int) -> int:
    n = len(text)
    while True:
        m = re.compile(r"\s*@(?!interface)[\w.]+").match(text, i)
        if not m:
            return i
        i = m.end()
        k = re.compile(r"\s*\(").match(text, i)
        if k:
            e = balanced(text, k.end() - 1)
            if e < 0:
                return n
            i = e


def _spring_paths(args: str) -> list[str]:
    for p in split_top(args):
        m = re.match(r"\s*(?:value|path)\s*=\s*(.*)$", p, re.S)
        if m:
            return _strs(m.group(1))
    parts = split_top(args)
    if parts and not re.match(r"\s*\w+\s*=", parts[0]):
        return _strs(parts[0])
    return []


def extract_spring(text: str, rel: str) -> list[Found]:
    lines = _Lines(text)
    events = []  # (pos, kind, payload)
    for m in _SPRING_ANN.finditer(text):
        args = ""
        k = re.compile(r"\s*\(").match(text, m.end())
        end = m.end()
        if k:
            e = balanced(text, k.end() - 1)
            if e < 0:
                continue
            args = text[k.end():e - 1]
            end = e
        after = _skip_annotations(text, end)
        is_class = bool(re.compile(r"\s*(?:(?:public|protected|private|abstract|final|static)\s+)*(?:class|interface)\s").match(text, after))
        events.append((m.start(), "map", (m.group(1), args, is_class)))
    for m in _CLASS_DECL.finditer(text):
        events.append((m.start() + (1 if text[m.start()] == "\n" else 0), "class", None))
    events.sort(key=lambda e: (e[0], 0 if e[1] == "map" else 1))
    out: list[Found] = []
    pending: list[str] | None = None
    prefixes = [""]
    for pos, kind, pl in events:
        if kind == "class":
            prefixes = [p for p in (pending or [""])]
            pending = None
            continue
        verb, args, is_class = pl
        paths = _spring_paths(args) or [""]
        if is_class:
            pending = paths if verb == "Request" else pending
            continue
        if verb == "Request":
            ms = re.findall(r"RequestMethod\.(\w+)", _kw(args, "method") or "") or ["ANY"]
        else:
            ms = [verb.upper()]
        ln = lines.of(pos)
        for pre in prefixes:
            for p in paths:
                for mth in ms:
                    out.append(_route(rel, ln, mth, join_path(pre, p)))
    return out


# ---------------------------------------------------------------- Flask / FastAPI
_PY_DECO = re.compile(r"^[ \t]*@(\w+)\.(route|api_route|get|post|put|patch|delete|options|head|websocket)\s*\(", re.M)
_PY_FACTORY = re.compile(r"^[ \t]*(\w+)\s*(?::[^=\n]+)?=\s*(?:\w+\.)*(Blueprint|APIRouter|FastAPI|Flask)\s*\(", re.M)
_PY_MOUNT = re.compile(r"\b(\w+)\.(register_blueprint|include_router)\s*\(")
_PY_RESTFUL = re.compile(r"\b\w+\.add_(?:org_)?resource\s*\(")  # Flask-RESTful: api.add_resource(Cls, "/a", "/b")


def extract_python_web(text: str, rel: str) -> list[Found]:
    if not re.search(r"flask|fastapi|Blueprint|APIRouter|starlette|quart", text, re.I):
        return []
    lines = _Lines(text)
    own: dict[str, str] = {}
    flask_like: dict[str, bool] = {}
    mounts: dict[str, tuple[str, str | None]] = {}
    for m in _PY_FACTORY.finditer(text):
        e = balanced(text, m.end() - 1)
        if e < 0:
            continue
        args = text[m.end():e - 1]
        var, kind = m.group(1), m.group(2)
        own[var] = (_kw(args, "url_prefix") if kind == "Blueprint" else _kw(args, "prefix")) or ""
        own[var] = (_strs(own[var]) or [""])[0] if own[var] else ""
        flask_like[var] = kind in ("Blueprint", "Flask")
    for m in _PY_MOUNT.finditer(text):
        e = balanced(text, m.end() - 1)
        if e < 0:
            continue
        parts = split_top(text[m.end():e - 1])
        if not parts or not re.fullmatch(r"\s*\w+\s*", parts[0]):
            continue
        child = parts[0].strip()
        pref = _kw(text[m.end():e - 1], "url_prefix" if m.group(2) == "register_blueprint" else "prefix")
        mounts[child] = (m.group(1), (_strs(pref) or [None])[0] if pref else None)

    def full(var: str, seen=()) -> str:
        if var in seen:
            return ""
        o = own.get(var, "")
        if var in mounts:
            parent, mp = mounts[var]
            base = full(parent, seen + (var,))
            if flask_like.get(var) and mp is not None:
                return join_path(base, mp)  # register_blueprint url_prefix overrides
            return join_path(base, mp or "", o)
        return join_path(o) if o else ""

    out: list[Found] = []
    for m in _PY_DECO.finditer(text):
        e = balanced(text, m.end() - 1)
        if e < 0:
            continue
        args = text[m.end():e - 1]
        var, verb = m.group(1), m.group(2)
        if verb == "websocket":
            continue
        path = _first_str_arg(args)
        if path is None:
            continue
        if var not in own and not re.search(r"\b" + var + r"\s*=", text):
            pass
        methods: list[str]
        if verb in ("route", "api_route"):
            ml = _kw(args, "methods")
            methods = [s.upper() for s in _strs(ml)] if ml else []
            if not methods:
                methods = ["GET"] if verb == "route" else ["ANY"]
        else:
            methods = [verb.upper()]
        ln = lines.of(m.start() + (len(m.group(0)) - len(m.group(0).lstrip())))
        for mth in methods:
            out.append(_route(rel, ln, mth, join_path(full(var), path)))
    for m in _PY_RESTFUL.finditer(text):
        e = balanced(text, m.end() - 1)
        if e < 0:
            continue
        parts = split_top(text[m.end():e - 1])
        if len(parts) < 2 or not re.fullmatch(r"\s*[\w.]+\s*", parts[0]):
            continue
        for part in parts[1:]:
            if re.match(r"\s*\w+\s*=", part):
                continue  # endpoint=..., resource_class_kwargs=...
            for u in _strs(part)[:1]:
                out.append(_route(rel, lines.of(m.start()), "ANY", join_path(u)))
    out.sort(key=lambda f: f.line)
    return out


# ---------------------------------------------------------------- Rails
_REST_PLURAL = [("index", "GET", ""), ("create", "POST", ""), ("new", "GET", "/new"), ("edit", "GET", "/:id/edit"),
                ("show", "GET", "/:id"), ("update", "PATCH", "/:id"), ("update", "PUT", "/:id"), ("destroy", "DELETE", "/:id")]
_REST_SINGULAR = [("create", "POST", ""), ("new", "GET", "/new"), ("edit", "GET", "/edit"),
                  ("show", "GET", ""), ("update", "PATCH", ""), ("update", "PUT", ""), ("destroy", "DELETE", "")]
_RB_VERBS = ("get", "post", "put", "patch", "delete")


def _rb_syms(s: str) -> list[str]:
    return re.findall(r""":(\w+)|['"]([^'"]+)['"]""", s) and [a or b for a, b in re.findall(r""":(\w+)|['"]([^'"]+)['"]""", s)]


def _rb_opt(rest: str, key: str) -> str | None:
    m = re.search(rf"(?:\b{key}:|:{key}\s*=>)\s*(\[[^\]]*\]|:\w+|'[^']*'|\"[^\"]*\")", rest)
    return m.group(1) if m else None


def extract_rails(text: str, rel: str) -> list[Found]:
    out: list[Found] = []
    # frame: dict(kind, child, member, collection)
    stack: list[dict] = [{"kind": "root", "child": ""}]
    for ln, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"(?<![\w'\"])#.*$", "", raw).strip()
        if not line:
            continue
        if re.match(r"end\b", line):
            if len(stack) > 1:
                stack.pop()
            continue
        opens = bool(re.search(r"\bdo(?:\s*\|[^|]*\|)?\s*$", line)) or bool(re.match(r"(?:if|unless|case|begin|while)\b", line))
        top = stack[-1]
        cur = top["child"]
        m = re.match(r"(namespace|scope|resources|resource|member|collection|root|match|concern|constraints|defaults|"
                     r"get|post|put|patch|delete)\b\s*(.*)$", line)
        if not m:
            if opens:
                stack.append({"kind": "other", "child": cur})
            continue
        word, rest = m.group(1), m.group(2)
        if word in ("namespace", "scope"):
            seg = ""
            if word == "namespace":
                s = _rb_syms(rest.split(",")[0])
                seg = s[0] if s else ""
                po = _rb_opt(rest, "path")
                seg = (_rb_syms(po) or [seg])[0] if po else seg
            else:
                po = _rb_opt(rest, "path")
                first = rest.split(",")[0].strip()
                if po:
                    seg = (_rb_syms(po) or [""])[0]
                elif first and not re.match(r"\w+:|:\w+\s*=>", first) and not first.startswith("{"):
                    seg = (_rb_syms(first) or [""])[0]
            if opens:
                stack.append({"kind": "scope", "child": join_path(cur, seg) if seg else cur})
            continue
        if word in ("constraints", "defaults", "concern"):
            if opens:
                stack.append({"kind": "other", "child": cur})
            continue
        if word in ("member", "collection"):
            if opens:
                src = next((f for f in reversed(stack) if f["kind"] == "resources"), None)
                p = (src or {}).get(word, cur)
                stack.append({"kind": "other", "child": p})
            continue
        if word in ("resources", "resource"):
            head = re.split(r",\s*(?=\w+:|:\w+\s*=>)", rest, 1)
            names = _rb_syms(head[0])
            opts = head[1] if len(head) > 1 else ""
            only, exc = _rb_opt(opts, "only"), _rb_opt(opts, "except")
            po = _rb_opt(opts, "path")
            table = _REST_PLURAL if word == "resources" else _REST_SINGULAR
            last = None
            for nm in names:
                seg = (_rb_syms(po) or [nm])[0] if po else nm
                base = join_path(cur, seg)
                acts = [a for a in table]
                if only:
                    keep = _rb_syms(only) if only.startswith("[") else ([only.strip(":")] if only.startswith(":") else [])
                    if only.strip(":") == "none" or only == ":none":
                        keep = []
                    acts = [a for a in acts if a[0] in keep]
                if exc:
                    ks = _rb_syms(exc) if exc.startswith("[") else [exc.strip(":")]
                    acts = [a for a in acts if a[0] not in ks]
                for _, mth, suffix in acts:
                    out.append(_route(rel, ln, mth, join_path(base, suffix) if suffix else base))
                if word == "resources":
                    last = {"kind": "resources", "child": f"{base}/:{nm.rstrip('s') or nm}_id",
                            "member": f"{base}/:id", "collection": base}
                else:
                    last = {"kind": "resources", "child": base, "member": base, "collection": base}
            if opens:
                stack.append(last or {"kind": "other", "child": cur})
            continue
        if word == "root":
            out.append(_route(rel, ln, "GET", join_path(cur)))
            if opens:
                stack.append({"kind": "other", "child": cur})
            continue
        # get/post/put/patch/delete/match
        head = re.split(r",\s*(?=\w+:|:\w+\s*=>)", rest, 1)
        first = head[0].strip()
        first = re.split(r"\s*=>", first)[0]
        opts = head[1] if len(head) > 1 else rest
        syms = _rb_syms(first.split(" do")[0]) if first and not re.match(r"\w+:", first) else []
        hm = re.search(r"=>\s*['\"]", rest)
        path = syms[0] if syms else None
        if path is None and not syms:
            if opens:
                stack.append({"kind": "other", "child": cur})
            continue
        on = _rb_opt(rest, "on")
        base = cur
        if on:
            src = next((f for f in reversed(stack) if f["kind"] == "resources"), None)
            if src:
                base = src.get(on.strip(":"), cur)
        po = _rb_opt(rest, "path")
        if po:
            path = (_rb_syms(po) or [path])[0]
        if word == "match":
            via = _rb_opt(rest, "via")
            if via and via.strip(":") != "all":
                methods = [v.upper() for v in (_rb_syms(via) if via.startswith("[") else [via.strip(":")])]
            else:
                methods = ["ANY"]
        else:
            methods = [word.upper()]
        for mth in methods:
            out.append(_route(rel, ln, mth, join_path(base, path)))
        if opens:
            stack.append({"kind": "other", "child": cur})
        _ = hm
    return out


# ---------------------------------------------------------------- ASP.NET
_CS_ATTR = re.compile(r"\[\s*(Http(?:Get|Post|Put|Patch|Delete|Head|Options)|Route)\b(?:\s*\(((?:[^()\"]|\"(?:[^\"\\]|\\.)*\")*)\))?\s*\]")
_CS_MIN = re.compile(r"\b(\w+)\.Map(Get|Post|Put|Patch|Delete)\s*\(\s*\"([^\"]*)\"")
_CS_GROUP = re.compile(r"\b(\w+)\s*=\s*(\w+)\.MapGroup\s*\(\s*\"([^\"]*)\"")


def _cs_template(args: str | None) -> str | None:
    if not args:
        return None
    m = re.match(r"\s*(?:template\s*=\s*)?\"((?:[^\"\\]|\\.)*)\"", args)
    return m.group(1) if m else None


def extract_aspnet(text: str, rel: str) -> list[Found]:
    lines = _Lines(text)
    out: list[Found] = []
    attrs = list(_CS_ATTR.finditer(text))
    # group consecutive attributes (whitespace-only gaps) and any other attributes in between
    clusters: list[list[re.Match]] = []
    for m in attrs:
        if clusters:
            gap = text[clusters[-1][-1].end():m.start()]
            if re.fullmatch(r"(?:\s|\[[^\]]*\])*", gap):
                clusters[-1].append(m)
                continue
        clusters.append([m])
    class_route: str | None = None
    ctrl = ""
    for cl in clusters:
        end = cl[-1].end()
        rest = text[end:end + 600]
        rest = re.sub(r"^(?:\s|\[[^\]]*\])+", "", rest)
        cm = re.match(r"(?:(?:public|internal|protected|private|abstract|sealed|partial|static)\s+)*class\s+(\w+)", rest)
        if cm:
            ctrl = cm.group(1)
            routes = [_cs_template(a.group(2)) for a in cl if a.group(1) == "Route"]
            class_route = next((r for r in routes if r is not None), None)
            continue
        mm = re.match(r"(?:(?:public|internal|protected|private|static|virtual|override|async|new)\s+)*[\w<>\[\],.?\s]+?\s+(\w+)\s*\(", rest)
        if not mm:
            continue
        action = mm.group(1)
        verbs = [a for a in cl if a.group(1).startswith("Http")]
        routes = [_cs_template(a.group(2)) for a in cl if a.group(1) == "Route"]
        mroute = next((r for r in routes if r is not None), None)
        ln = lines.of(verbs[0].start()) if verbs else lines.of(cl[0].start())
        for v in verbs:
            tmpl = _cs_template(v.group(2))
            if tmpl is None:
                tmpl = mroute
            cname = re.sub(r"Controller$", "", ctrl)
            def sub(t: str) -> str:
                return t.replace("[controller]", cname).replace("[action]", action)
            if tmpl is not None and tmpl.startswith(("/", "~/")):
                path = join_path(sub(tmpl.lstrip("~")))
            else:
                path = join_path(sub(class_route or ""), sub(tmpl or ""))
            out.append(_route(rel, ln, v.group(1)[4:], path))
    # minimal APIs
    gp: dict[str, tuple[str, str]] = {m.group(1): (m.group(2), m.group(3)) for m in _CS_GROUP.finditer(text)}

    def gfull(v: str, seen=()) -> str:
        if v in gp and v not in seen:
            return join_path(gfull(gp[v][0], seen + (v,)), gp[v][1])
        return ""
    for m in _CS_MIN.finditer(text):
        out.append(_route(rel, lines.of(m.start()), m.group(2), join_path(gfull(m.group(1)), m.group(3))))
    out.sort(key=lambda f: f.line)
    return out



# ---------------------------------------------------------------- Django
_DJ_CALL = re.compile(r"(?<![\w.])(path|re_path|url)\s*\(")
_DJ_REGISTER = re.compile(r"\b\w+\.register\s*\(")
_DJ_GROUP = re.compile(r"\(\?P<(\w+)>(?:[^()\\]|\\.|\([^()]*\))*\)")


def _django_regex_to_path(rx: str) -> str:
    rx = _DJ_GROUP.sub(lambda m: "{" + m.group(1) + "}", rx)
    rx = rx.lstrip("^")
    rx = re.sub(r"\$$", "", rx)
    rx = rx.replace("/?", "").replace("\\.", ".")
    return rx


def extract_django(text: str, rel: str) -> list[Found]:
    """URLconf routes (path/re_path/url) and DRF router.register, per file.

    Prefixes added by an ``include()`` in another file are not visible here, so paths are relative to
    the URLconf they are declared in.
    """
    if not re.search(r"urlpatterns|\.register\s*\(", text):
        return []
    # docstrings and comments hold example URLconfs (Django's urls.py template does): blank them, keep offsets
    text = re.sub(r'(\"\"\"|\'\'\').*?\1', lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    text = re.sub(r"(?m)#.*$", lambda m: " " * len(m.group(0)), text)
    lines = _Lines(text)
    out: list[Found] = []
    for m in _DJ_CALL.finditer(text):
        e = balanced(text, m.end() - 1)
        if e < 0:
            continue
        args = text[m.end():e - 1]
        parts = split_top(args)
        if len(parts) < 2:
            continue
        view = parts[1].strip()
        if view.startswith("include") or re.search(r"\.urls\b", view):
            continue  # a mount, not an endpoint
        route = _first_str_arg(args, ("route", "regex"))
        if route is None:
            continue
        if m.group(1) == "path":
            # verbatim, as the original inventory regex and recorded eval graphs name them
            out.append(_route(rel, lines.of(m.start()), "ANY", route))
        else:
            out.append(_route(rel, lines.of(m.start()), "ANY", join_path(_django_regex_to_path(route))))
    for m in _DJ_REGISTER.finditer(text):
        e = balanced(text, m.end() - 1)
        if e < 0:
            continue
        pref = _first_str_arg(text[m.end():e - 1], ("prefix",))
        if pref:
            out.append(_route(rel, lines.of(m.start()), "ANY", join_path(_django_regex_to_path(pref))))
    out.sort(key=lambda f: f.line)
    return out


# ---------------------------------------------------------------- dispatch
def extract_routes(rel: str, text: str) -> list[Found]:
    """Route facts for one file, or [] if no framework extractor applies. Never raises."""
    low = rel.lower()
    try:
        if low.endswith(".java"):
            return extract_spring(text, rel)
        if low.endswith(".py"):
            return extract_python_web(text, rel) + (extract_django(text, rel) if "urlpatterns" in text or ".register(" in text else [])
        if low.endswith(".cs"):
            return extract_aspnet(text, rel)
        if low.endswith("routes.rb") or "/routes/" in low and low.endswith(".rb"):
            return extract_rails(text, rel)
    except Exception:  # noqa: BLE001 - extractors must degrade gracefully
        return []
    return []
