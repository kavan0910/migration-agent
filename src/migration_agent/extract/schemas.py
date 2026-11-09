"""D-01 schema extractors -> schema_entity facts (static, never executes code).

Public API:
    extract_schemas(rel_path, text) -> list[Found]   # dispatches on path/extension, [] if not applicable
    extract_flyway / extract_alembic / extract_prisma / extract_typeorm (text, rel) -> list[Found]

Each Found has kind="schema_entity", name=<table/model/entity>, line=<definition line> and
attrs={"source": "flyway|alembic|prisma|typeorm", "op": "create|alter", "columns": [{"name","type","line"}]}.
"""
from __future__ import annotations

import ast
import re

from .frameworks import Found, _Lines, balanced, split_top

_CONSTRAINT = re.compile(r"^(?:constraint|primary\s+key|foreign\s+key|unique|index|key|check|fulltext|spatial|exclude|like|period)\b", re.I)
_TYPE_END = re.compile(r"\s+(?:not\s+null|null|default|primary|references|unique|check|auto_increment|autoincrement|generated|"
                       r"collate|constraint|comment|identity|on\s+update|as)\b.*$", re.I | re.S)
_IDENT = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[\w$]+)'
_QNAME = rf"{_IDENT}(?:\s*\.\s*{_IDENT})*"


def _unq(s: str) -> str:
    return re.sub(r'["`\[\]]', "", s).replace(" ", "")


def _blank_sql_comments(t: str) -> str:
    t = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), t, flags=re.S)
    return re.sub(r"--[^\n]*", lambda m: " " * len(m.group(0)), t)


def _sql_col(defn: str) -> tuple[str, str] | None:
    defn = " ".join(defn.split())
    if not defn or _CONSTRAINT.match(defn):
        return None
    m = re.match(rf"({_IDENT})\s+(.*)$", defn)
    if not m:
        return None
    typ = _TYPE_END.sub("", m.group(2)).strip()
    return _unq(m.group(1)), typ


def extract_flyway(text: str, rel: str) -> list[Found]:
    t = _blank_sql_comments(text)
    lines = _Lines(t)
    out: list[Found] = []
    for m in re.finditer(rf"\bCREATE\s+(?:(?:GLOBAL\s+|LOCAL\s+)?(?:TEMP(?:ORARY)?\s+)|UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_QNAME})\s*\(", t, re.I):
        e = balanced(t, m.end() - 1)
        if e < 0:
            continue
        cols = []
        body_start = m.end()
        pos = body_start
        for piece in split_top(t[body_start:e - 1]):
            c = _sql_col(piece)
            if c:
                lead = len(piece) - len(piece.lstrip())
                cols.append({"name": c[0], "type": c[1], "line": lines.of(pos + lead)})
            pos += len(piece) + 1
        out.append(Found("schema_entity", _unq(m.group(1)), rel, lines.of(m.start()),
                         {"source": "flyway", "op": "create", "columns": cols}))
    for m in re.finditer(rf"\bALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?({_QNAME})\s+([^;]*)", t, re.I):
        body = m.group(2)
        cols = []
        for piece in split_top(body):
            am = re.match(rf"\s*ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(.*)$", piece, re.I | re.S)
            if not am or re.match(r"\s*(?:constraint|primary|foreign|unique|index|key|check)\b", am.group(1), re.I):
                continue
            c = _sql_col(am.group(1))
            if c:
                cols.append({"name": c[0], "type": c[1], "line": lines.of(m.start(2) + body.find(piece))})
        if cols:
            out.append(Found("schema_entity", _unq(m.group(1)), rel, lines.of(m.start()),
                             {"source": "flyway", "op": "alter", "columns": cols}))
    return out


def _dotted(n: ast.AST) -> str:
    try:
        return ast.unparse(n)
    except Exception:  # noqa: BLE001
        return ""


def _alembic_col(call: ast.Call) -> dict | None:
    if _dotted(call.func).rsplit(".", 1)[-1] != "Column" or not call.args:
        return None
    a0 = call.args[0]
    if not (isinstance(a0, ast.Constant) and isinstance(a0.value, str)):
        return None
    typ = ""
    if len(call.args) > 1:
        typ = _dotted(call.args[1])
    for kw in call.keywords:
        if kw.arg == "type_":
            typ = _dotted(kw.value)
    return {"name": a0.value, "type": typ, "line": call.lineno}


def extract_alembic(text: str, rel: str) -> list[Found]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    out: list[Found] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = _dotted(node.func)
        if fn not in ("op.create_table", "op.add_column") or not node.args:
            continue
        a0 = node.args[0]
        if not (isinstance(a0, ast.Constant) and isinstance(a0.value, str)):
            continue
        if fn == "op.create_table":
            cols = [c for c in (_alembic_col(a) for a in node.args[1:] if isinstance(a, ast.Call)) if c]
            out.append(Found("schema_entity", a0.value, rel, node.lineno, {"source": "alembic", "op": "create", "columns": cols}))
        else:
            c = _alembic_col(node.args[1]) if len(node.args) > 1 and isinstance(node.args[1], ast.Call) else None
            if c:
                out.append(Found("schema_entity", a0.value, rel, node.lineno, {"source": "alembic", "op": "alter", "columns": [c]}))
    out.sort(key=lambda f: (f.line, f.name))
    return out


def extract_prisma(text: str, rel: str) -> list[Found]:
    out: list[Found] = []
    cur: Found | None = None
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.split("//")[0].strip()
        if cur is None:
            m = re.match(r"model\s+(\w+)\s*\{", line)
            if m:
                cur = Found("schema_entity", m.group(1), rel, ln, {"source": "prisma", "op": "create", "columns": []})
                if line.endswith("}"):
                    out.append(cur)
                    cur = None
            continue
        if line.startswith("}"):
            out.append(cur)
            cur = None
            continue
        if not line or line.startswith("@@"):
            continue
        m = re.match(r"(\w+)\s+([\w]+(?:\([^)]*\))?(?:\[\]|\?)?)", line)
        if m:
            cur.attrs["columns"].append({"name": m.group(1), "type": m.group(2), "line": ln})
    return out


_TO_COL = re.compile(r"@(?:Column|PrimaryGeneratedColumn|PrimaryColumn|CreateDateColumn|UpdateDateColumn|DeleteDateColumn|VersionColumn|ObjectIdColumn)\b")


def extract_typeorm(text: str, rel: str) -> list[Found]:
    if "@Entity" not in text:
        return []
    lines = _Lines(text)
    out: list[Found] = []
    for m in re.finditer(r"@Entity\b", text):
        i = m.end()
        table = None
        if text[i:i + 1] == "(":
            e = balanced(text, i)
            if e < 0:
                continue
            sm = re.search(r"""^\(\s*['"]([^'"]+)['"]|\bname\s*:\s*['"]([^'"]+)['"]""", text[i:e])
            table = (sm.group(1) or sm.group(2)) if sm else None
            i = e
        cm = re.compile(r"(?:\s|@\w+(?:\([^)]*\))?)*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)[^{]*\{").match(text, i)
        if not cm:
            continue
        b0 = cm.end() - 1
        b1 = balanced(text, b0, "{", "}")
        if b1 < 0:
            b1 = len(text)
        body = text[b0:b1]
        cols = []
        for cmat in _TO_COL.finditer(body):
            j = cmat.end()
            dbtype = None
            if body[j:j + 1] == "(":
                e = balanced(body, j)
                if e < 0:
                    continue
                tm = re.search(r"""^\(\s*['"](\w[\w ]*)['"]|\btype\s*:\s*['"](\w[\w ]*)['"]""", body[j:e])
                dbtype = (tm.group(1) or tm.group(2)) if tm else None
                j = e
            while True:  # skip stacked decorators
                dm = re.compile(r"\s*@\w+").match(body, j)
                if not dm:
                    break
                j = dm.end()
                if body[j:j + 1] == "(":
                    e = balanced(body, j)
                    if e < 0:
                        break
                    j = e
            fm = re.compile(r"\s*(?:(?:public|private|protected|readonly|declare)\s+)*(\w+)\s*[!?]?\s*(?::\s*([^;=\n]+))?").match(body, j)
            if not fm:
                continue
            typ = (fm.group(2) or dbtype or "").strip()
            col = {"name": fm.group(1), "type": typ, "line": lines.of(b0 + cmat.start())}
            if dbtype and dbtype != typ:
                col["db_type"] = dbtype
            cols.append(col)
        attrs = {"source": "typeorm", "op": "create", "columns": cols}
        if table:
            attrs["table"] = table
        out.append(Found("schema_entity", cm.group(1), rel, lines.of(m.start()), attrs))
    return out


_FLYWAY_NAME = re.compile(r"^V[\w.]+__.+\.sql$", re.I)


def extract_schemas(rel: str, text: str) -> list[Found]:
    """Schema entity facts for one file, or [] if no schema extractor applies. Never raises."""
    low = rel.lower()
    base = low.rsplit("/", 1)[-1]
    try:
        if _FLYWAY_NAME.match(base):
            return extract_flyway(text, rel)
        if base == "schema.prisma" or base.endswith(".prisma"):
            return extract_prisma(text, rel)
        if low.endswith(".py") and re.search(r"(?:^|/)versions/[^/]+\.py$", low) and "op." in text:
            return extract_alembic(text, rel)
        if low.endswith((".ts", ".js")) and "@Entity" in text:
            return extract_typeorm(text, rel)
    except Exception:  # noqa: BLE001
        return []
    return []
