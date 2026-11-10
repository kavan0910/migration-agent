from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from .. import audit
from ..errors import ApiError
from ..models import Job, Run, RunEvent
from ..serializers import Page, run_out
from ..services import runs as run_service
from ..tenancy import ProjectCtx, RunCtx, project_ctx, run_ctx

router = APIRouter()


class RunIn(BaseModel):
    type: Literal["analyze", "verify", "reanalyze"] = "analyze"
    model_profile: Literal["fast", "balanced", "best"] = "balanced"
    max_usd: float | None = Field(default=None, gt=0)
    incremental: bool = False


@router.post("/v1/projects/{pid}/runs", status_code=202)
def create_run(body: RunIn, response: Response, request: Request, ctx: ProjectCtx = Depends(project_ctx),
               idempotency_key: str | None = Header(default=None, max_length=200)):
    ctx.require("member")
    run, created = run_service.create_run(
        ctx.db, project=ctx.project, user_id=ctx.user.id, type_=body.type, profile=body.model_profile,
        max_usd=body.max_usd, incremental=body.incremental, idempotency_key=idempotency_key,
        settings=request.app.state.settings)
    ctx.db.commit()
    if not created:
        response.status_code = 200
    return run_out(run)


@router.get("/v1/projects/{pid}/runs")
def list_runs(page: Page = Depends(), status: str | None = None, ctx: ProjectCtx = Depends(project_ctx)):
    cond = [Run.project_id == ctx.project.id]
    if status:
        cond.append(Run.status == status)
    total = ctx.db.scalar(select(func.count()).select_from(Run).where(*cond))
    rows = ctx.db.scalars(select(Run).where(*cond).order_by(Run.created_at.desc(), Run.id)
                          .limit(page.limit).offset(page.offset)).all()
    return {"items": [run_out(r) for r in rows], "total": total}


@router.get("/v1/runs/{rid}")
def get_run(ctx: RunCtx = Depends(run_ctx)):
    return run_out(ctx.run)


@router.post("/v1/runs/{rid}/cancel")
def cancel_run(ctx: RunCtx = Depends(run_ctx)):
    ctx.require("member")
    db, run = ctx.db, ctx.run
    if run.status in run_service.TERMINAL:
        raise ApiError(409, "conflict", f"run already {run.status}")
    if run.status == "queued":
        # conditional update so a worker that just claimed it wins the race
        n = db.execute(update(Job).where(Job.run_id == run.id, Job.status == "queued").values(status="cancelled")).rowcount
        if n:
            run.status, run.finished_at = "cancelled", datetime.now(timezone.utc)
            run_service.add_event(db, run.id, run.workspace_id, "status", {"status": "cancelled"})
    if run.status in run_service.ACTIVE:
        run.cancel_requested = True
        run_service.add_event(db, run.id, run.workspace_id, "status", {"status": "cancel_requested"})
    audit.record(db, action="run.cancel", workspace_id=run.workspace_id, actor=ctx.user.id, target=run.id)
    db.commit()
    return run_out(run)


@router.get("/v1/runs/{rid}/events")
def run_events(request: Request, ctx: RunCtx = Depends(run_ctx), after: int | None = Query(None, ge=0),
               last_event_id: str | None = Header(default=None)):
    """Server-Sent Events. Replays stored events after ``after`` / ``Last-Event-ID`` then follows live until the run
    reaches a terminal state, then emits ``event: end``."""
    st = request.app.state.settings
    dbh = request.app.state.db
    rid, wid = ctx.run.id, ctx.run.workspace_id
    start = after
    if start is None and last_event_id and last_event_id.isdigit():
        start = int(last_event_id)
    start = start or 0

    def gen():
        seq, beat, t0 = start, time.monotonic(), time.monotonic()
        while time.monotonic() - t0 < 3600:
            with dbh.session() as s:
                rows = s.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.workspace_id == wid,
                                                        RunEvent.seq > seq).order_by(RunEvent.seq).limit(200)).all()
                status = s.scalar(select(Run.status).where(Run.id == rid))
                data = [(e.seq, e.type, e.payload) for e in rows]
            for sq, typ, payload in data:
                seq = sq
                yield f"id: {sq}\nevent: {typ}\ndata: {json.dumps(payload)}\n\n"
                beat = time.monotonic()
            if data:
                continue
            if status is None or status in run_service.TERMINAL:
                yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                return
            if time.monotonic() - beat > st.sse_heartbeat_s:
                yield ": ping\n\n"
                beat = time.monotonic()
            time.sleep(st.sse_poll_s)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
