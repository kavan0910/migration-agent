"""Extract configuration keys (with secret redaction) from a code tree (A-05)."""

from __future__ import annotations

import configparser
import fnmatch
import json
import os
import re
import tomllib
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

REDACTED = "<redacted>"
PREVIEW_MAX = 60
MAX_FILE_BYTES = 1_000_000
SKIP_DIRS = {
    ".git", "node_modules", "target", "build", "dist", ".venv", "venv", "vendor",
    "__pycache__", ".gradle", ".idea", "bin", "obj", ".tox",
}

_SECRET_KEY = re.compile(
    r"(pass(word|wd|phrase)?|pwd|secret|token|api[_.-]?key|apikey|private[_.-]?key|"
    r"credential|access[_.-]?key|auth(?!or)|connection[_.-]?string|dsn|signing|salt|bearer|cert(ificate)?[_.-]?key)",
    re.I)
_SECRET_VALUE = re.compile(
    r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|://[^/@\s:]+:[^@\s]+@|"
    r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bsk-[A-Za-z0-9]{16,}|\bxox[abp]-[A-Za-z0-9-]{10,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.)")


class ConfigKey(BaseModel):
    file: str
    line: int
    key: str
    value_preview: str | None = None
    is_secret: bool = False


class ConfigFacts(BaseModel):
    files: list[str] = Field(default_factory=list)
    keys: list[ConfigKey] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


def make_key(file: str, line: int, key: str, value) -> ConfigKey:
    if value is None:
        sval = None
    elif isinstance(value, (dict, list)):
        sval = f"<{type(value).__name__}>"
    else:
        sval = str(value).strip().strip("'\"")
    secret = bool(_SECRET_KEY.search(key)) or bool(sval and _SECRET_VALUE.search(sval))
    if secret:
        preview = REDACTED if sval else None  # never keep the real value
    else:
        preview = None if sval is None else (sval if len(sval) <= PREVIEW_MAX else sval[:PREVIEW_MAX] + "...")
    return ConfigKey(file=file, line=line, key=key, value_preview=preview, is_secret=secret)


# ---------------------------------------------------------------- parsers
# each returns list[(key, line, value)]

def _kv_lines(text: str, sep_re: str, comment: tuple[str, ...]) -> list[tuple[str, int, str | None]]:
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(comment):
            continue
        if line.startswith("export "):
            line = line[7:]
        mo = re.match(rf"([^\s=:#][^=:]*?)\s*{sep_re}\s*(.*)$", line)
        if mo:
            v = mo.group(2)
            if sep_re == "=" and not v.startswith(("'", '"')):
                v = re.split(r"\s+#", v, 1)[0]
            out.append((mo.group(1).strip(), n, v.strip()))
    return out


def _parse_env(text: str):
    return _kv_lines(text, "=", ("#",))


def _parse_properties(text: str):
    return _kv_lines(text, "[=:]", ("#", "!"))


def _walk_yaml(node, prefix, out, in_env=False):
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            kname = str(k.value)
            path = f"{prefix}.{kname}" if prefix else kname
            if isinstance(v, yaml.ScalarNode):
                out.append((path, k.start_mark.line + 1, None if v.tag.endswith("null") else v.value))
            else:
                _walk_yaml(v, path, out, in_env=kname == "environment")
    elif isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            if isinstance(item, yaml.ScalarNode):
                if in_env and "=" in item.value:
                    k, _, v = item.value.partition("=")
                    out.append((f"{prefix}.{k}", item.start_mark.line + 1, v))
                elif in_env:
                    out.append((f"{prefix}.{item.value}", item.start_mark.line + 1, None))
                else:
                    out.append((f"{prefix}[{i}]", item.start_mark.line + 1, item.value))
            else:
                _walk_yaml(item, f"{prefix}[{i}]", out)


def _parse_yaml(text: str):
    out: list = []
    for doc in yaml.compose_all(text):
        if doc is not None:
            _walk_yaml(doc, "", out)
    return out


def _parse_json(text: str):
    data = json.loads(text)
    lines = text.splitlines()
    cursor = [0]
    out: list = []

    def find_line(k: str) -> int:
        pat = f'"{k}"'
        for i in range(cursor[0], len(lines)):
            if pat in lines[i]:
                cursor[0] = i
                return i + 1
        return cursor[0] + 1

    def walk(o, prefix):
        if isinstance(o, dict):
            for k, v in o.items():
                path = f"{prefix}.{k}" if prefix else k
                ln = find_line(k)
                if isinstance(v, (dict, list)):
                    walk(v, path)
                else:
                    out.append((path, ln, v))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                if isinstance(v, (dict, list)):
                    walk(v, f"{prefix}[{i}]")
                else:
                    out.append((f"{prefix}[{i}]", cursor[0] + 1, v))

    walk(data, "")
    return out


def _parse_toml(text: str):
    tomllib.loads(text)  # validate
    out, section = [], ""
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        mo = re.match(r"\[\[?([^\]]+)\]\]?", line)
        if mo:
            section = mo.group(1).strip()
            continue
        mo = re.match(r'([A-Za-z0-9_."-]+)\s*=\s*(.*)$', line)
        if mo:
            v = re.split(r"\s+#", mo.group(2), 1)[0].strip()
            out.append((f"{section}.{mo.group(1)}" if section else mo.group(1), n, v))
    return out


def _parse_ini(text: str):
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read_string(text)  # validate
    out, section = [], ""
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        mo = re.match(r"\[([^\]]+)\]", line)
        if mo:
            section = mo.group(1)
            continue
        mo = re.match(r"([^=:\s][^=:]*?)\s*[=:]\s*(.*)$", line)
        if mo:
            out.append((f"{section}.{mo.group(1)}" if section else mo.group(1), n, mo.group(2)))
    return out


def _classify(name: str):
    low = name.lower()
    if low == ".env" or low.startswith(".env.") or low.endswith(".env"):
        return _parse_env
    if low.endswith(".properties"):
        return _parse_properties
    if fnmatch.fnmatch(low, "docker-compose*.y*ml") or fnmatch.fnmatch(low, "compose*.y*ml") \
            or low.endswith((".yml", ".yaml")):
        return _parse_yaml
    if fnmatch.fnmatch(low, "appsettings*.json"):
        return _parse_json
    if low == "config.toml":
        return _parse_toml
    if low.endswith((".ini", ".cfg")) and low != "setup.cfg":
        return _parse_ini
    return None


def extract_config(code_dir: Path) -> ConfigFacts:
    code_dir = Path(code_dir)
    facts = ConfigFacts()
    for dirpath, dirnames, filenames in os.walk(code_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            parser = _classify(fn)
            if parser is None:
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(code_dir).as_posix()
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    facts.errors[rel] = "skipped: file too large"
                    continue
                items = parser(p.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:  # malformed: record, keep going
                facts.errors[rel] = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"
                continue
            facts.files.append(rel)
            facts.keys.extend(make_key(rel, ln, k, v) for k, ln, v in items)
    facts.files.sort()
    facts.keys.sort(key=lambda c: (c.file, c.line, c.key))
    facts.errors = dict(sorted(facts.errors.items()))
    return facts


def write_config(facts: ConfigFacts, out: Path) -> Path:
    """Write facts as JSON. `out` may be a facts directory or a file path."""
    out = Path(out)
    if out.suffix != ".json":
        out = out / "config.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(facts.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return out
