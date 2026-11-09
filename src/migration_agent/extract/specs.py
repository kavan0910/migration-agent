"""D-01 API spec parsers: OpenAPI 3 / Swagger 2 (json, yaml) and Protocol Buffers (.proto).

Public API:
    extract_specs(rel_path, text) -> (list[Found], list[str])   # (facts, warnings); never raises
    parse_openapi_text(text, rel, is_yaml) / parse_proto(text, rel)

OpenAPI operations -> route Found named "METHOD /path (operationId)" (the operationId suffix is
presentation only; identity is the part before it, see ``route_identity``). Proto services ->
route Found named "RPC Service/Method"; messages -> schema_entity. Inputs larger than MAX_SPEC_BYTES
are skipped with a warning. Nothing is executed (json.loads / yaml.safe_load only).
"""
from __future__ import annotations

import json
import re

from .frameworks import Found, _Lines

MAX_SPEC_BYTES = 2 * 1024 * 1024
_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_OPID_SUFFIX = re.compile(r" \(([^()\s]+)\)$")


def route_identity(name: str) -> str:
    """Strip the presentation-only ' (operationId)' suffix from an endpoint name."""
    return _OPID_SUFFIX.sub("", name)


def _find_line(lines: list[str], start: int, pattern: str) -> int | None:
    rx = re.compile(pattern)
    for i in range(start, len(lines)):
        if rx.search(lines[i]):
            return i
    return None


def parse_openapi_text(text: str, rel: str, is_yaml: bool) -> tuple[list[Found], list[str]]:
    if len(text.encode("utf-8", "ignore")) > MAX_SPEC_BYTES:
        return [], [f"{rel}: spec larger than {MAX_SPEC_BYTES} bytes, skipped"]
    if is_yaml:
        if not re.search(r"^\s*['\"]?(openapi|swagger)['\"]?\s*:", text, re.M):
            return [], []
        try:
            import yaml
        except ImportError:
            return [], [f"{rel}: PyYAML not installed, YAML OpenAPI spec skipped"]
        try:
            doc = yaml.safe_load(text)
        except Exception as e:  # noqa: BLE001
            return [], [f"{rel}: malformed YAML ({type(e).__name__})"]
    else:
        if '"openapi"' not in text and '"swagger"' not in text:
            return [], []
        try:
            doc = json.loads(text)
        except (ValueError, RecursionError) as e:
            return [], [f"{rel}: malformed JSON ({type(e).__name__})"]
    if not isinstance(doc, dict) or not ("openapi" in doc or "swagger" in doc) or not isinstance(doc.get("paths"), dict):
        return [], []
    base = ""
    if "swagger" in doc and isinstance(doc.get("basePath"), str) and doc["basePath"] not in ("", "/"):
        base = "/" + doc["basePath"].strip("/")
    lines = text.splitlines()
    out: list[Found] = []
    for path, item in doc["paths"].items():
        if not isinstance(path, str) or not isinstance(item, dict):
            continue
        pl = _find_line(lines, 0, r"""^\s*['"]?""" + re.escape(path) + r"""['"]?\s*:""")
        for meth in _METHODS:
            op = item.get(meth)
            if not isinstance(op, dict):
                continue
            ml = _find_line(lines, pl or 0, rf"""^\s*['"]?{meth}['"]?\s*:""") if pl is not None else None
            ln = (ml if ml is not None else (pl if pl is not None else 0)) + 1
            full = (base + (path if path.startswith("/") else "/" + path)) or "/"
            name = f"{meth.upper()} {full}"
            opid = op.get("operationId")
            if isinstance(opid, str) and opid and not re.search(r"[()\s]", opid):
                name += f" ({opid})"
            out.append(Found("route", name, rel, ln, {"source": "openapi"}))
    out.sort(key=lambda f: (f.line, f.name))
    return out, []


def parse_proto(text: str, rel: str) -> tuple[list[Found], list[str]]:
    if len(text.encode("utf-8", "ignore")) > MAX_SPEC_BYTES:
        return [], [f"{rel}: proto larger than {MAX_SPEC_BYTES} bytes, skipped"]
    t = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    t = re.sub(r"//[^\n]*", lambda m: " " * len(m.group(0)), t)
    lines = _Lines(t)
    out: list[Found] = []
    stack: list[tuple[str, str, Found | None]] = []  # (kind, name, entity)
    buf_start = 0
    i, n, q = 0, len(t), None
    warnings: list[str] = []

    def stmt_line(a: int, b: int) -> int:
        seg = t[a:b]
        return lines.of(a + (len(seg) - len(seg.lstrip())))

    while i < n:
        c = t[i]
        if q:
            if c == "\\":
                i += 1
            elif c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c in "{;}":
            seg = t[buf_start:i]
            sl = stmt_line(buf_start, i)
            top = stack[-1] if stack else None
            if c == "{":
                m = re.match(r"\s*(message|service|enum|oneof|extend)\s+([\w.]+)", seg)
                r = re.match(r"\s*rpc\s+(\w+)\s*\(\s*(stream\s+)?([\w.]+)\s*\)\s*returns\s*\(\s*(stream\s+)?([\w.]+)\s*\)", seg)
                if r and top and top[0] == "service":
                    out.append(Found("route", f"RPC {top[1]}/{r.group(1)}", rel, sl,
                                     {"source": "proto", "request": r.group(3), "response": r.group(5)}))
                    stack.append(("rpc", r.group(1), None))
                elif m and m.group(1) == "message":
                    parent = next((s for s in reversed(stack) if s[0] == "message"), None)
                    nm = f"{parent[1]}.{m.group(2)}" if parent else m.group(2)
                    ent = Found("schema_entity", nm, rel, sl, {"source": "proto", "op": "create", "columns": []})
                    out.append(ent)
                    stack.append(("message", nm, ent))
                elif m and m.group(1) == "oneof":
                    stack.append(("oneof", m.group(2), top[2] if top else None))
                elif m:
                    stack.append((m.group(1), m.group(2), None))
                else:
                    stack.append(("other", "", None))
            elif c == ";":
                if top and top[0] == "service":
                    r = re.match(r"\s*rpc\s+(\w+)\s*\(\s*(stream\s+)?([\w.]+)\s*\)\s*returns\s*\(\s*(stream\s+)?([\w.]+)\s*\)", seg)
                    if r:
                        out.append(Found("route", f"RPC {top[1]}/{r.group(1)}", rel, sl,
                                         {"source": "proto", "request": r.group(3), "response": r.group(5)}))
                elif top and top[0] in ("message", "oneof") and top[2] is not None:
                    fm = re.match(r"\s*(?:(?:repeated|optional|required)\s+)?(map\s*<[^>]*>|[\w.]+)\s+(\w+)\s*=\s*\d+", seg)
                    if fm and fm.group(1) not in ("option", "reserved", "extensions"):
                        top[2].attrs["columns"].append({"name": fm.group(2), "type": re.sub(r"\s+", "", fm.group(1)), "line": sl})
            else:  # }
                if stack:
                    stack.pop()
            buf_start = i + 1
        i += 1
    out.sort(key=lambda f: (f.line, f.name))
    return out, warnings


def extract_specs(rel: str, text: str) -> tuple[list[Found], list[str]]:
    low = rel.lower()
    try:
        if low.endswith(".proto"):
            return parse_proto(text, rel)
        if low.endswith(".json"):
            return parse_openapi_text(text, rel, False)
        if low.endswith((".yaml", ".yml")):
            return parse_openapi_text(text, rel, True)
    except Exception as e:  # noqa: BLE001
        return [], [f"{rel}: spec parse failed ({type(e).__name__})"]
    return [], []
