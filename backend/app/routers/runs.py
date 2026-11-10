from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from .. import audit
from ..errors import ApiError
from ..db import set_rls_context
from ..deps import _bearer, current_user
from ..models import Job, Membership, Run, RunEvent, User
from ..security import AuthError
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


def _authorize_stream(request: Request, rid: str) -> tuple[str, str, str]:
    """Authenticate + authorise in a short-lived session (closed before streaming starts, so an open stream never pins
    a pooled database connection). Returns (user_id, workspace_id, bearer token)."""
    s = request.app.state.db.sessionmaker()
    try:
        user = current_user(request, s)
        ctx = run_ctx(rid, user, s)
        return user.id, ctx.run.workspace_id, _bearer(request)
    finally:
        s.rollback()
        s.close()


def _stream_allowed(dbh, verifier, user_id: str, wid: str, token: str) -> bool:
    """Re-validated every ``sse_recheck_s``: token still valid/unexpired, account active, still a member."""
    try:
        verifier.verify(token)
    except AuthError:
        return False
    with dbh.session() as s:
        active = s.scalar(select(User.is_active).where(User.id == user_id))
        member = s.scalar(select(Membership.id).where(Membership.workspace_id == wid, Membership.user_id == user_id))
        return bool(active) and member is not None


def _fetch_events(dbh, rid: str, wid: str, seq: int):
    with dbh.session() as s:
        set_rls_context(s, [wid])
        rows = s.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.workspace_id == wid,
                                                RunEvent.seq > seq).order_by(RunEvent.seq).limit(200)).all()
        status = s.scalar(select(Run.status).where(Run.id == rid))
        return [(e.seq, e.type, e.payload) for e in rows], status


@router.get("/v1/runs/{rid}/events")
async def run_events(rid: str, request: Request, after: int | None = Query(None, ge=0),
                     last_event_id: str | None = Header(default=None)):
    """Server-Sent Events. Replays stored events after ``after`` / ``Last-Event-ID`` then follows live until the run
    reaches a terminal state, then emits ``event: end``.

    Asynchronous on purpose: a stream waits with ``asyncio.sleep`` and touches the database only through short
    threadpool calls, so thousands of idle streams cannot starve the API of worker threads. Bounded by per-user and
    per-run stream caps (429), a maximum lifetime (the client reconnects with Last-Event-ID) and a periodic
    re-check of membership and token expiry (a removed viewer or an expired token is cut off with ``event: end``)."""
    st = request.app.state.settings
    dbh, verifier, reg = request.app.state.db, request.app.state.verifier, request.app.state.sse
    user_id, wid, token = await run_in_threadpool(_authorize_stream, request, rid)
    lease = reg.acquire(user_id, rid, st.sse_max_per_user, st.sse_max_per_run, st.sse_max_lifetime_s + 30)
    if lease is None:
        raise ApiError(429, "rate_limited", "too many open event streams", {"Retry-After": "5"})
    start = after
    if start is None and last_event_id and last_event_id.isdigit():
        start = int(last_event_id)
    start = start or 0

    def end(status: str | None, reason: str | None = None) -> str:
        return f"event: end\ndata: {json.dumps({'status': status, **({'reason': reason} if reason else {})})}\n\n"

    async def gen():
        try:
            seq = start
            t0 = beat = last_check = time.monotonic()
            while True:
                now = time.monotonic()
                if now - t0 >= st.sse_max_lifetime_s:
                    yield end(None, "lifetime")
                    return
                if now - last_check >= st.sse_recheck_s:
                    last_check = now
                    if not await run_in_threadpool(_stream_allowed, dbh, verifier, user_id, wid, token):
                        yield end(None, "revoked")
                        return
                data, status = await run_in_threadpool(_fetch_events, dbh, rid, wid, seq)
                for sq, typ, payload in data:
                    seq = sq
                    yield f"id: {sq}\nevent: {typ}\ndata: {json.dumps(payload)}\n\n"
                    beat = time.monotonic()
                if data:
                    continue
                if status is None or status in run_service.TERMINAL:
                    yield end(status)
                    return
                if time.monotonic() - beat > st.sse_heartbeat_s:
                    yield ": ping\n\n"
                    beat = time.monotonic()
                await asyncio.sleep(st.sse_poll_s)
        finally:
            reg.release(lease)

    return StreamingResponse(gen(), media_type="text/event-stream", background=BackgroundTask(reg.release, lease),
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
