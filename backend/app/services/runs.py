from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, jobs
from ..billing import can_start_run
from ..config import Settings
from ..errors import ApiError
from ..models import Artifact, Project, Run, RunEvent, Source
from ..profiles import PROFILES

RUN_TYPES = ("analyze", "verify", "reanalyze")
ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "failed", "stopped_budget", "cancelled")


def add_event(db: Session, run_id: str, workspace_id: str, type_: str, payload: dict) -> RunEvent:
    """Append an event with the next seq. Callers serialise per run (API: row insert; worker: its own lock)."""
    for _ in range(5):
        seq = (db.scalar(select(func.coalesce(func.max(RunEvent.seq), 0)).where(RunEvent.run_id == run_id)) or 0) + 1
        ev = RunEvent(run_id=run_id, workspace_id=workspace_id, seq=seq, type=type_, payload=payload)
        try:
            with db.begin_nested():
                db.add(ev)
                db.flush()
            return ev
        except IntegrityError:
            continue
    raise RuntimeError("could not allocate event sequence")


def has_analysis(db: Session, project_id: str) -> bool:
    return db.scalar(select(Artifact.id).where(Artifact.project_id == project_id, Artifact.kind == "document")
                     .limit(1)) is not None


def create_run(db: Session, *, project: Project, user_id: str | None, type_: str, profile: str,
               max_usd: float | None, incremental: bool, idempotency_key: str | None, settings: Settings,
               auto: bool = False) -> tuple[Run, bool]:
    """Validate, apply entitlements (can_start_run) and enqueue. Returns (run, created)."""
    if type_ not in RUN_TYPES:
        raise ApiError(422, "invalid_request", f"type must be one of {', '.join(RUN_TYPES)}")
    if profile not in PROFILES:
        raise ApiError(422, "invalid_request", f"model_profile must be one of {', '.join(PROFILES)}")
    if idempotency_key:
        prev = db.scalar(select(Run).where(Run.project_id == project.id, Run.idempotency_key == idempotency_key))
        if prev is not None:
            return prev, False
    budget = settings.default_max_usd if max_usd is None else float(max_usd)
    if not (0 < budget <= settings.hard_max_usd):
        raise ApiError(422, "invalid_request", f"max_usd must be > 0 and <= {settings.hard_max_usd}")
    src = db.scalar(select(Source).where(Source.project_id == project.id).order_by(Source.created_at.desc()))
    if type_ == "analyze" and src is None:
        raise ApiError(409, "no_source", "upload a source archive or connect a git url first")
    if type_ in ("verify", "reanalyze") and not has_analysis(db, project.id):
        raise ApiError(409, "no_analysis", "run an analysis first")
    q = select(Run.id).where(Run.project_id == project.id, Run.status.in_(ACTIVE), Run.type == type_)
    if db.scalar(q.limit(1)):
        if auto:
            raise ApiError(409, "run_in_progress", "a run of this type is already queued")
        raise ApiError(409, "run_in_progress", f"a {type_} run is already queued or running for this project")
    est = project.estimate if (project.estimate and project.estimate.get("profile") == profile) else None
    estimate = {"chunks": (est or {}).get("chunks", 0), "usd": (est or {}).get("usd", budget)}
    decision = can_start_run(db, project.workspace_id, estimate, budget)
    if not decision.allowed:
        raise ApiError(403, "entitlement_denied", decision.reason)
    run = Run(workspace_id=project.workspace_id, project_id=project.id, type=type_, status="queued",
              model_profile=profile, max_usd=budget, incremental=incremental, estimate_usd=estimate["usd"],
              idempotency_key=idempotency_key, created_by=user_id, completed_stages=[])
    db.add(run)
    db.flush()
    jobs.enqueue(db, run, settings.job_max_attempts)
    add_event(db, run.id, run.workspace_id, "status", {"status": "queued"})
    audit.record(db, action="run.create", workspace_id=project.workspace_id, actor=user_id, target=run.id,
                 meta={"project_id": project.id, "type": type_, "profile": profile, "max_usd": budget, "auto": auto})
    return run, True
