from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import audit
from ..models import (Artifact, Comment, Job, Project, Resolution, Run, RunEvent, Source, UsageEvent, UserDoc)
from ..config import Settings, get_settings
from ..storage import ObjectStore, project_prefix
from . import deletion


def delete_project(db: Session, store: ObjectStore, project: Project, actor: str, settings: Settings | None = None) -> dict:
    """Remove every row and object of a project; write the audit record in the same transaction.

    usage_events are workspace-level metering: they are kept but detached (project_id/run_id NULLed) so no row
    references the deleted project. A deletion tombstone (``deletion_tasks``) is written in the SAME transaction as the
    row delete, so object removal is never "forgotten": it is attempted right after commit and, if the store fails,
    retried by the worker's reaper until confirmed (then audited as ``storage.purge``).
    """
    pid, wid = project.id, project.workspace_id
    run_ids = list(db.scalars(select(Run.id).where(Run.project_id == pid)))
    counts = {"runs": len(run_ids),
              "artifacts": db.scalar(select(func.count()).select_from(Artifact).where(Artifact.project_id == pid)) or 0,
              "user_docs": db.scalar(select(func.count()).select_from(UserDoc).where(UserDoc.project_id == pid)) or 0}
    if run_ids:
        db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
        db.execute(delete(Job).where(Job.run_id.in_(run_ids)))
    db.execute(update(UsageEvent).where(UsageEvent.project_id == pid).values(project_id=None, run_id=None))
    for model in (Artifact, Comment, Resolution, UserDoc, Source, Run):
        db.execute(delete(model).where(model.project_id == pid))
    db.execute(delete(Project).where(Project.id == pid))
    audit.record(db, action="project.delete", workspace_id=wid, actor=actor, target=pid, meta=counts)
    task = deletion.enqueue(db, prefix=project_prefix(wid, pid), workspace_id=wid, project_id=pid)
    db.commit()
    done = deletion.attempt(db, store, task, settings or get_settings())
    db.commit()
    return {**counts, "objects_deleted": done, "objects_pending": not done}
