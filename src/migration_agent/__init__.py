from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("migration-agent")  # single source of truth: pyproject.toml
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
