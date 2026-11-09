"""Golden-file helper. Regenerate with:  UPDATE_GOLDEN=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/e2e"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"


def updating() -> bool:
    return os.environ.get("UPDATE_GOLDEN", "").lower() not in ("", "0", "false", "no")


def write(name: str, data) -> None:
    GOLDEN.mkdir(exist_ok=True)
    (GOLDEN / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def load(name: str):
    p = GOLDEN / name
    if not p.exists():
        pytest.fail(f"missing golden file {p}; create it with UPDATE_GOLDEN=1 (see tests/e2e/README.md)")
    return json.loads(p.read_text())


def check(name: str, data) -> None:
    """Exact-match golden. With UPDATE_GOLDEN=1 the file is (re)written instead."""
    data = json.loads(json.dumps(data))      # normalise tuples etc.
    if updating():
        write(name, data)
        return
    assert data == load(name), f"{name} drifted; if intended, regenerate with UPDATE_GOLDEN=1"
