"""Secret scanning. Findings never contain the secret value."""
from __future__ import annotations

import re

from pydantic import BaseModel

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")),
    (
        "generic_secret",
        re.compile(
            r"""(?ix)\b[\w.-]*(?:api[_-]?key|apikey|secret|password|passwd|token)[\w.-]*
            ["']?\s*[:=]\s*["']?(?!\$\{|<|\{\{|%\(|os\.|process\.|env\b|getenv|environ)[^\s"'#\[\](){},;]{8,}"""
        ),
    ),
]


class SecretFinding(BaseModel):
    file: str
    line: int
    kind: str
    preview: str  # source line with the matched secret replaced by [REDACTED:kind]


def _redact(line: str, hits: list[tuple[str, re.Match[str]]]) -> str:
    spans = sorted((m.start(), m.end(), k) for k, m in hits)
    out, pos = [], 0
    for s, e, k in spans:
        if s < pos:
            continue
        out.append(line[pos:s])
        out.append(f"[REDACTED:{k}]")
        pos = e
    out.append(line[pos:])
    text = "".join(out).strip()
    return text[:120] + ("..." if len(text) > 120 else "")


def scan_text(relpath: str, text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for no, line in enumerate(text.splitlines(), 1):
        if len(line) > 5000:
            line = line[:5000]
        hits = [(k, m) for k, p in PATTERNS for m in p.finditer(line)]
        if not hits:
            continue
        preview = _redact(line, hits)
        for kind in dict.fromkeys(k for k, _ in hits):
            findings.append(SecretFinding(file=relpath, line=no, kind=kind, preview=preview))
    return findings
