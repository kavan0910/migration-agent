"""Durable object-store deletion: tombstones written with the row delete, a reaper that retries until the store
confirms, and a reconciler for prefixes that no project row owns (write-object-before-row races, failed rollbacks).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import Settings
from ..db import Database, set_rls_context
from ..models import DeletionTask, Project
from ..storage import ObjectStore

log = logging.getLogger("app.deletion")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(db: Session, *, prefix: str, workspace_id: str | None, project_id: str | None,
            reason: str = "project.delete") -> DeletionTask:
    """Add a tombstone in the CALLER's transaction (so it exists iff the row deletion commits)."""
    t = db.scalar(select(DeletionTask).where(DeletionTask.prefix == prefix))
    if t is None:
        t = DeletionTask(prefix=prefix, workspace_id=workspace_id, project_id=project_id, reason=reason,
                         attempts=0, next_try_at=_now())
        db.add(t)
        db.flush()
    return t


def attempt(db: Session, store: ObjectStore, task: DeletionTask, settings: Settings) -> bool:
    """Try once. Success removes the tombstone and writes the audit record; failure schedules a backoff retry."""
    try:
        removed = store.delete_prefix(task.prefix)
    except Exception as e:  # noqa: BLE001 - any store failure means "not confirmed gone"
        task.attempts += 1
        delay = min(settings.deletion_retry_max_s, settings.deletion_retry_base_s * (2 ** min(task.attempts - 1, 10)))
        task.next_try_at = _now() + timedelta(seconds=delay)
        task.last_error = type(e).__name__ + ": " + str(e)[:300]
        log.error("object deletion failed (attempt %s): %s", task.attempts, type(e).__name__,
                  extra={"event": "deletion_failed"})
        return False
    audit.record(db, action="storage.purge", workspace_id=task.workspace_id, actor=None, target=task.project_id or "",
                 meta={"objects": removed, "attempts": task.attempts + 1, "reason": task.reason})
    db.delete(task)
    return True


def reconcile_orphans(db: Session, store: ObjectStore) -> int:
    """Queue prefixes that hold objects but belong to no project row. A project row always exists before the first
    object is written, so such a prefix is leftover from a delete that raced an upload (or a lost delete)."""
    n = 0
    for prefix in store.list_project_prefixes():
        parts = prefix.strip("/").split("/")
        if len(parts) != 2:
            continue
        wid, pid = parts
        if db.scalar(select(Project.id).where(Project.id == pid)) is None:
            enqueue(db, prefix=prefix, workspace_id=wid, project_id=pid, reason="orphan")
            n += 1
    return n


def reap(dbh: Database, store: ObjectStore, settings: Settings, *, reconcile: bool = False, limit: int = 50) -> dict:
    """One reaper pass (called from the worker loop). Returns counters for logs / tests."""
    out = {"orphans": 0, "done": 0, "failed": 0}
    with dbh.session() as s:
        set_rls_context(s, None, bypass=True)
        if reconcile:
            out["orphans"] = reconcile_orphans(s, store)
    with dbh.session() as s:
        set_rls_context(s, None, bypass=True)
        due = s.scalars(select(DeletionTask).where(DeletionTask.next_try_at <= _now())
                        .order_by(DeletionTask.next_try_at).limit(limit)).all()
        for t in due:
            out["done" if attempt(s, store, t, settings) else "failed"] += 1
    return out
