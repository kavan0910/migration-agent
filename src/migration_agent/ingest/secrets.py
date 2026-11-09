"""Secret scanning and redaction. Findings never contain the secret value.

``redact_text`` is the single choke point: ingest runs it on every file it copies, so
code/ (and everything derived from it: facts, prompts, graph, docs, checkpoints) holds
``[REDACTED:<kind>]`` placeholders instead of live credentials.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any

from pydantic import BaseModel

# (kind, regex, group) -- ``group`` is the capture group holding the secret (0 = whole match).
PATTERNS: list[tuple[str, re.Pattern[str], int]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), 0),
    ("slack_token", re.compile(r"\bxox[abeoprs]-[A-Za-z0-9-]{10,}"), 0),
    ("stripe_key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{10,}"), 0),
    ("gcp_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), 0),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), 0),
    ("db_url_password", re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s:/@'\"<>]*:([^\s@/'\"<>]{3,})@"), 1),
    (
        "generic_secret",
        re.compile(
            r"""(?ix)\b[\w.-]*(?:api[_-]?key|apikey|secret|password|passwd|token)[\w.-]*
            ["']?[ \t]*[:=][ \t]*["']?(?!\$\{|<|\{\{|%\(|os\.|process\.|env\b|getenv|environ|\[REDACTED)
            ([^\s"'#\[\](){},;]{8,})"""
        ),
        1,
    ),
]
# multi-line: BEGIN ... PRIVATE KEY through END marker (or end of text if truncated)
_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.S)

SENSITIVE_FILE_GLOBS = (
    ".env", ".env.*", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "*.pem", "*.key", "*.p12", "*.pfx",
    "*.jks", "*.keystore", ".netrc", ".pgpass", ".htpasswd", "credentials", ".npmrc", ".pypirc",
)
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".tmpl")


def is_secret_filename(name: str) -> bool:
    """True for files that exist to hold credentials (withheld entirely at ingest)."""
    low = name.lower()
    if low.endswith(_TEMPLATE_SUFFIXES):
        return False
    return any(fnmatch.fnmatchcase(low, g) for g in SENSITIVE_FILE_GLOBS)


class SecretFinding(BaseModel):
    file: str
    line: int
    kind: str
    preview: str  # source line with the matched secret replaced by [REDACTED:kind]


def _placeholder(kind: str) -> str:
    return f"[REDACTED:{kind}]"


def _line_spans(line: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    window, step = 5000, 4500  # bounded work per regex call; overlap > any secret length
    starts = range(0, max(len(line), 1), step) if len(line) > window else [0]
    for off in starts:
        seg = line[off:off + window]
        for kind, rx, g in PATTERNS:
            for m in rx.finditer(seg):
                if m.group(g) is None:
                    continue
                spans.append((off + m.start(g), off + m.end(g), kind))
    return spans


def _merge(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for s, e, k in sorted(spans):
        if out and s < out[-1][1]:
            ps, pe, pk = out[-1]
            out[-1] = (ps, max(pe, e), pk)
        else:
            out.append((s, e, k))
    return out


def _apply(text: str, spans: list[tuple[int, int, str]]) -> str:
    out, pos = [], 0
    for s, e, k in spans:
        out.append(text[pos:s])
        out.append(_placeholder(k))
        pos = e
    out.append(text[pos:])
    return "".join(out)


def _preview(line: str, spans: list[tuple[int, int, str]]) -> str:
    text = _apply(line, _merge(spans)).strip()
    return text[:120] + ("..." if len(text) > 120 else "")


def redact_text(relpath: str, text: str) -> tuple[str, list[SecretFinding]]:
    """Return (text with secrets replaced by placeholders, findings without values)."""
    findings: list[SecretFinding] = []
    # 1) private-key blocks (multi-line)
    block_spans = [(m.start(), m.end(), "private_key") for m in _KEY_BLOCK.finditer(text)]
    for s, _e, _k in block_spans:
        findings.append(SecretFinding(file=relpath, line=text.count("\n", 0, s) + 1, kind="private_key",
                                      preview=_placeholder("private_key")))
    # 2) per-line patterns
    spans: list[tuple[int, int, str]] = list(block_spans)
    pos = 0
    for no, raw in enumerate(text.splitlines(keepends=True), 1):
        line = raw.rstrip("\r\n")
        ls = _line_spans(line)
        if ls:
            pv = _preview(line, ls)
            for kind in dict.fromkeys(k for _, _, k in ls):
                findings.append(SecretFinding(file=relpath, line=no, kind=kind, preview=pv))
            spans.extend((pos + s, pos + e, k) for s, e, k in ls)
        pos += len(raw)
    if not spans:
        return text, findings
    findings.sort(key=lambda f: (f.line, f.kind))
    return _apply(text, _merge(spans)), findings


def scan_text(relpath: str, text: str) -> list[SecretFinding]:
    return redact_text(relpath, text)[1]


def redact_string(text: str) -> str:
    """Convenience for free text (audit fields, comments, user docs)."""
    return redact_text("", text)[0]


def redact_obj(obj: Any) -> Any:
    """Recursively redact every string in a JSON-like structure."""
    if isinstance(obj, str):
        return redact_string(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
