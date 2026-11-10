"""DB-backed job queue (contract section 5).

Postgres: ``SELECT ... FOR UPDATE SKIP LOCKED``. SQLite: a single atomic ``UPDATE ... WHERE id = (SELECT ...)
RETURNING`` (SQLite serialises writers, so two workers can never claim the same row). Stale ``running`` jobs (worker
died; no heartbeat within ``job_lock_timeout_s``) are re-queued by ``reap_stale``.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from .models import Job, Project, Run


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(db: Session, run: Run, max_attempts: int = 3) -> Job:
    job = Job(run_id=run.id, workspace_id=run.workspace_id, status="queued", max_attempts=max_attempts,
              available_at=utcnow())
    db.add(job)
    db.flush()
    return job


def claim(db: Session, worker_id: str) -> Job | None:
    now = utcnow()
    if db.get_bind().dialect.name == "postgresql":
        job = db.scalar(select(Job).where(Job.status == "queued", Job.available_at <= now)
                        .order_by(Job.available_at, Job.created_at).limit(1).with_for_update(skip_locked=True))
        if job is None:
            db.rollback()
            return None
        job.status, job.locked_by, job.locked_at, job.attempts = "running", worker_id, now, job.attempts + 1
        db.commit()
        return job
    row = db.execute(text(
        "UPDATE jobs SET status='running', locked_by=:w, locked_at=:now, attempts=attempts+1 "
        "WHERE id = (SELECT id FROM jobs WHERE status='queued' AND available_at <= :now "
        "ORDER BY available_at, created_at LIMIT 1) AND status='queued' RETURNING id"),
        {"w": worker_id, "now": now}).first()
    db.commit()
    if row is None:
        return None
    db.expire_all()
    return db.get(Job, row[0])


def heartbeat(db: Session, job_id: str) -> None:
    db.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(locked_at=utcnow()))
    db.commit()


def reap_stale(db: Session, timeout_s: int) -> int:
    """Re-queue (or fail, when attempts are exhausted) jobs whose worker stopped heart-beating."""
    cutoff = utcnow() - timedelta(seconds=timeout_s)
    stale = db.scalars(select(Job).where(Job.status == "running", Job.locked_at < cutoff)).all()
    for j in stale:
        run = db.get(Run, j.run_id)
        if run is not None and run.project_id:
            proj = db.get(Project, run.project_id)
            if proj is not None and proj.active_run_id == run.id:
                proj.active_run_id = None
        if j.attempts >= j.max_attempts:
            j.status, j.last_error = "failed", "worker lost"
            if run is not None and run.status in ("queued", "running"):
                run.status, run.error, run.finished_at = "failed", "worker lost", utcnow()
        else:
            j.status, j.locked_by, j.available_at = "queued", None, utcnow()
            if run is not None and run.status == "running":
                run.status = "queued"
    db.commit()
    return len(stale)


def backoff_delay(attempts: int, base: float, cap: float = 900.0) -> float:
    return min(cap, base * (2 ** max(0, attempts - 1))) * (0.8 + 0.4 * random.random())


def try_lock_project(db: Session, project_id: str, run_id: str) -> bool:
    """Compare-and-set on the project row: one executing run per project (state is a single archive)."""
    r = db.execute(update(Project).where(Project.id == project_id,
                                         (Project.active_run_id.is_(None)) | (Project.active_run_id == run_id))
                   .values(active_run_id=run_id))
    db.commit()
    return r.rowcount == 1


def unlock_project(db: Session, project_id: str, run_id: str) -> None:
    db.execute(update(Project).where(Project.id == project_id, Project.active_run_id == run_id)
               .values(active_run_id=None))
    db.commit()
