"""Shared helpers for the review package: atomic JSON writes, timestamps."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class ReviewError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
