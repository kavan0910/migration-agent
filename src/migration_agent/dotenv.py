"""Minimal project-local .env loader (no dependency, never overrides existing env)."""
from __future__ import annotations

import os
from pathlib import Path


def parse_env_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[7:].lstrip()
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key, value = key.strip(), value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    else:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def load_dotenv(path: Path) -> list[str]:
    """Load KEY=VALUE pairs from path; returns the keys actually set."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    loaded: list[str] = []
    for raw in text.splitlines():
        kv = parse_env_line(raw)
        if kv and kv[0] not in os.environ:
            os.environ[kv[0]] = kv[1]
            loaded.append(kv[0])
    return loaded


def project_root() -> Path:
    """Repo root (directory holding pyproject.toml), found from this file; falls back to cwd."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()
