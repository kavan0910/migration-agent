"""Tree-sitter based structural fact extraction (facts/ast.json)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from tree_sitter_language_pack import get_parser

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    "target", "bin", "obj", ".idea", ".mypy_cache", ".pytest_cache",
}

EXT_LANG = {
    ".py": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".java": "java", ".go": "go", ".cs": "csharp",
}
# Languages we recognise but have no grammar wired for -> fallback.
FALLBACK_LANG = {
    ".cbl": "cobol", ".cob": "cobol", ".cobol": "cobol", ".cpy": "cobol",
    ".pl1": "pl1", ".pli": "pl1", ".pl": "perl", ".rpg": "rpg", ".rpgle": "rpg",
    ".jcl": "jcl", ".f": "fortran", ".f90": "fortran", ".vb": "vb",
    ".bas": "basic", ".asm": "assembly", ".rb": "ruby", ".php": "php",
    ".sql": "sql", ".c": "c", ".cpp": "cpp", ".h": "c", ".scala": "scala",
    ".kt": "kotlin", ".swift": "swift", ".rs": "rust",
}
# Reported language for grammar-backed tsx/jsx variants.
REPORT_LANG = {"tsx": "typescript"}


class ClassFact(BaseModel):
    name: str
    kind: str = "class"
    start_line: int
    end_line: int
    decorators: list[str] = Field(default_factory=list)


class FunctionFact(BaseModel):
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    params: list[str] = Field(default_factory=list)
    decorators: list[str] = Field(default_factory=list)


class FileFacts(BaseModel):
    path: str
    language: str
    parse_status: Literal["ok", "partial", "fallback"]
    line_count: int = 0
    classes: list[ClassFact] = Field(default_factory=list)
    functions: list[FunctionFact] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)


class AstFacts(BaseModel):
    files: list[FileFacts] = Field(default_factory=list)
    languages: dict[str, int] = Field(default_factory=dict)


# ---- per-language node configuration -------------------------------------
_CLASS = {
    "python": {"class_definition": "class"},
    "javascript": {"class_declaration": "class"},
    "typescript": {"class_declaration": "class", "abstract_class_declaration": "class",
                   "interface_declaration": "interface", "enum_declaration": "enum"},
    "java": {"class_declaration": "class", "interface_declaration": "interface",
             "enum_declaration": "enum", "record_declaration": "record"},
    "go": {},  # handled via type_spec
    "csharp": {"class_declaration": "class", "interface_declaration": "interface",
               "struct_declaration": "struct", "enum_declaration": "enum",
               "record_declaration": "record"},
}
_FUNC = {
    "python": {"function_definition"},
    "javascript": {"function_declaration", "method_definition", "generator_function_declaration"},
    "typescript": {"function_declaration", "method_definition", "generator_function_declaration",
                   "abstract_method_signature", "method_signature"},
    "java": {"method_declaration", "constructor_declaration"},
    "go": {"function_declaration", "method_declaration"},
    "csharp": {"method_declaration", "constructor_declaration"},
}
_IMPORT = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "java": {"import_declaration"},
    "go": {"import_spec"},
    "csharp": {"using_directive"},
}
_NAMESPACE = {"namespace_declaration", "file_scoped_namespace_declaration"}


def _grammar(lang: str) -> str:
    return lang


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf8", "replace")


def _name_of(node, src: bytes) -> str | None:
    n = node.child_by_field_name("name")
    if n is not None:
        return _text(n, src)
    for c in node.children:
        if c.type in ("identifier", "type_identifier", "property_identifier", "field_identifier"):
            return _text(c, src)
    return None


def _deco_name(node, src: bytes) -> str | None:
    """Name of a decorator/annotation/attribute node, without '@' or arguments."""
    t = _text(node, src).strip()
    t = t.lstrip("@[").strip()
    for i, ch in enumerate(t):
        if not (ch.isalnum() or ch in "_.$"):
            t = t[:i]
            break
    return t or None


def _decorators(node, src: bytes, lang: str) -> list[str]:
    out: list[str] = []

    def add(n):
        nm = _deco_name(n, src)
        if nm:
            out.append(nm)

    def scan(parent):
        for c in parent.children:
            if c.type in ("decorator", "marker_annotation", "annotation"):
                add(c)
            elif c.type == "attribute_list":
                for a in c.children:
                    if a.type == "attribute":
                        add(a)
            elif c.type == "modifiers":
                scan(c)

    scan(node)
    p = node.parent
    if p is not None:
        # TS: decorators preceding a method are siblings in class_body
        prev = node.prev_sibling
        while prev is not None and prev.type in ("export", "default"):
            prev = prev.prev_sibling
        pre = []
        while prev is not None and prev.type == "decorator":
            pre.append(prev)
            prev = prev.prev_sibling
        for d in reversed(pre):
            add(d)
    return out


def _params(node, src: bytes, lang: str) -> list[str]:
    pn = node.child_by_field_name("parameters")
    if pn is None:
        return []
    out = []
    for c in pn.named_children:
        if c.type == "comment":
            continue
        out.append(" ".join(_text(c, src).split()))
    return out


def _import_text(node, src: bytes, lang: str) -> str:
    t = " ".join(_text(node, src).split()).rstrip(";")
    if lang == "go":
        return t
    return t


class _Walker:
    def __init__(self, src: bytes, lang: str):
        self.src, self.lang = src, lang
        self.classes: list[ClassFact] = []
        self.functions: list[FunctionFact] = []
        self.imports: list[str] = []
        self.has_error = False

    def _add_func(self, node, name, scope, recv=None):
        q = ".".join([*scope, name]) if not recv else f"{recv}.{name}"
        self.functions.append(FunctionFact(
            name=name, qualified_name=q,
            start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
            params=_params(node, self.src, self.lang),
            decorators=_decorators(node, self.src, self.lang)))

    def walk(self, node, scope: list[str]):
        lang, src = self.lang, self.src
        t = node.type
        if node.is_error or node.is_missing:
            self.has_error = True
        if t in _IMPORT[lang]:
            self.imports.append(_import_text(node, src, lang))
            return
        if t in _NAMESPACE:
            nm = _name_of(node, src)
            inner = [*scope, nm] if nm else scope
            for c in node.children:
                self.walk(c, inner)
            return
        if t in _CLASS[lang]:
            nm = _name_of(node, src)
            if nm:
                self.classes.append(ClassFact(
                    name=nm, kind=_CLASS[lang][t],
                    start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                    decorators=_decorators(node, src, lang)))
                for c in node.children:
                    self.walk(c, [*scope, nm])
                return
        if lang == "go" and t == "type_spec":
            nm = _name_of(node, src)
            ty = node.child_by_field_name("type")
            if nm and ty is not None and ty.type in ("struct_type", "interface_type"):
                self.classes.append(ClassFact(
                    name=nm, kind=ty.type.split("_")[0],
                    start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))
        if t in _FUNC[lang]:
            nm = _name_of(node, src)
            if nm:
                recv = None
                if lang == "go" and t == "method_declaration":
                    r = node.child_by_field_name("receiver")
                    if r is not None:
                        rt = _text(r, src).strip("()").split()[-1].lstrip("*")
                        recv = rt.split("[")[0]
                self._add_func(node, nm, scope, recv)
                for c in node.children:
                    self.walk(c, [*scope, nm])
                return
        if lang in ("javascript", "typescript") and t == "variable_declarator":
            v = node.child_by_field_name("value")
            if v is not None and v.type in ("arrow_function", "function_expression", "function"):
                nm = _name_of(node, src)
                if nm:
                    self.functions.append(FunctionFact(
                        name=nm, qualified_name=".".join([*scope, nm]),
                        start_line=node.start_point[0] + 1, end_line=node.end_point[0] + 1,
                        params=_params(v, src, lang), decorators=[]))
                    return
        for c in node.children:
            self.walk(c, scope)


def extract_file(path: Path, rel: str) -> FileFacts | None:
    ext = path.suffix.lower()
    try:
        data = path.read_bytes()
    except OSError:
        return None
    lines = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    grammar = EXT_LANG.get(ext)
    if grammar is None:
        lang = FALLBACK_LANG.get(ext)
        if lang is None:
            return None
        return FileFacts(path=rel, language=lang, parse_status="fallback", line_count=lines)
    report = REPORT_LANG.get(grammar, grammar)
    try:
        parser = get_parser(_grammar(grammar))
        tree = parser.parse(data)
    except Exception:
        return FileFacts(path=rel, language=report, parse_status="fallback", line_count=lines)
    walk_lang = "typescript" if grammar == "tsx" else grammar
    w = _Walker(data, walk_lang)
    w.walk(tree.root_node, [])
    status = "partial" if (tree.root_node.has_error or w.has_error) else "ok"
    return FileFacts(
        path=rel, language=report, parse_status=status, line_count=lines,
        classes=sorted(w.classes, key=lambda c: (c.start_line, c.name)),
        functions=sorted(w.functions, key=lambda f: (f.start_line, f.qualified_name)),
        imports=sorted(set(w.imports)))


def extract_ast(code_dir: Path) -> AstFacts:
    code_dir = Path(code_dir)
    files: list[FileFacts] = []
    for root, dirs, names in os.walk(code_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            p = Path(root) / n
            ff = extract_file(p, p.relative_to(code_dir).as_posix())
            if ff is not None:
                files.append(ff)
    files.sort(key=lambda f: f.path)
    langs: dict[str, int] = {}
    for f in files:
        langs[f.language] = langs.get(f.language, 0) + 1
    return AstFacts(files=files, languages=dict(sorted(langs.items())))


def write_ast_facts(facts: AstFacts, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts.model_dump(), indent=2, sort_keys=True) + "\n")
