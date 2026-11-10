"""Adapter between the backend and ``migration_agent.service`` (platform contract section 2).

The real module is imported lazily. Tests (and any alternative engine) inject an object with the same functions via
``set_engine``; nothing else in the backend imports ``migration_agent`` directly.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Protocol


class EngineUnavailable(RuntimeError):
    pass


class Engine(Protocol):
    version: str

    def run_stage(self, project_dir: Path, stage: str, **kw: Any) -> Any: ...
    def estimate(self, project_dir: Path, config: dict | None = None, incremental: bool = False) -> dict: ...
    def review_state(self, project_dir: Path) -> dict: ...
    def graph_snapshot(self, project_dir: Path, version: int | None = None) -> dict: ...
    def graph_versions(self, project_dir: Path) -> list[dict]: ...
    def add_comment(self, project_dir: Path, item_id: str, text: str, author: str | None = None) -> dict: ...
    def add_resolution(self, project_dir: Path, finding_id: str, status: str, note: str = "",
                       author: str | None = None) -> dict: ...
    def document(self, project_dir: Path) -> str: ...
    def changelog(self, project_dir: Path) -> str: ...
    def pack_project(self, project_dir: Path, *, include_cache: bool = True) -> bytes: ...
    def unpack_project(self, data: bytes, dest: Path) -> None: ...


class ModuleEngine:
    """Delegates to a module exposing the section-2 functions (default: ``migration_agent.service``)."""

    def __init__(self, module: Any):
        self._m = module
        self.version = str(getattr(module, "__engine_version__", None) or _pkg_version())

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return getattr(self._m, name)
        except AttributeError:
            raise EngineUnavailable(f"engine does not provide {name}") from None


def _pkg_version() -> str:
    try:
        import migration_agent
        return str(getattr(migration_agent, "__version__", "unknown"))
    except Exception:  # pragma: no cover
        return "unknown"


_engine: Any = None


def set_engine(engine: Any | None) -> None:
    global _engine
    _engine = engine


def get_engine() -> Any:
    global _engine
    if _engine is None:
        try:
            _engine = ModuleEngine(importlib.import_module("migration_agent.service"))
        except ImportError as e:
            raise EngineUnavailable("migration_agent.service is not installed") from e
    return _engine
