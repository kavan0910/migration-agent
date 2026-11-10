"""Append-only audit trail. No update or delete code path exists for AuditLog rows."""
from __future__ import annotations

from sqlalchemy.orm import Session

from .models import AuditLog

_SENSITIVE = ("password", "token", "secret", "key", "authorization", "content", "source")


def _clean(meta: dict | None) -> dict:
    out = {}
    for k, v in (meta or {}).items():
        if any(s in k.lower() for s in _SENSITIVE):
            continue
        out[k] = v if isinstance(v, (int, float, bool, type(None))) else str(v)[:300]
    return out


def record(db: Session, *, action: str, workspace_id: str | None, actor: str | None, target: str = "",
           meta: dict | None = None) -> None:
    db.add(AuditLog(workspace_id=workspace_id, actor=actor, action=action, target=target, meta=_clean(meta)))
