"""Shared red-team helpers: fixture generator loader and a recording LLM harness."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_maker():
    spec = importlib.util.spec_from_file_location("make_redteam", ROOT / "scripts" / "make_redteam.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_redteam"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def mk():
    return _load_maker()
