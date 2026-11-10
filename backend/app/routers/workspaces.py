from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..billing import check_count_limit, consumption, effective_limits
from ..deps import current_user, get_db
from ..errors import ApiError, forbidden, not_found
from ..models import AuditLog, Membership, UsageEvent, User, Workspace
from ..serializers import Page, audit_out, member_out, workspace_out
from ..services import accounts
from ..tenancy import RANK, WorkspaceCtx, workspace_ctx

router = APIRouter()
Role = Literal["owner", "admin", "member", "viewer"]


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberIn(BaseModel):
    email: EmailStr
    role: Role = "member"


@router.get("/v1/workspaces")
def list_workspaces(page: Page = Depends(), user: User = Depends(current_user), db: Session = Depends(get_db)):
    base = (select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.execute(base.order_by(Workspace.created_at).limit(page.limit).offset(page.offset)).all()
    return {"items": [workspace_out(w, r) for w, r in rows], "total": total}


@router.post("/v1/workspaces", status_code=201)
def create_workspace(body: WorkspaceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ws = accounts.create_workspace(db, body.name, user)
    db.commit()
    return workspace_out(ws, "owner")


@router.get("/v1/workspaces/{wid}")
def get_workspace(ctx: WorkspaceCtx = Depends(workspace_ctx)):
    return workspace_out(ctx.workspace, ctx.role)


@router.get("/v1/workspaces/{wid}/members")
def list_members(page: Page = Depends(), ctx: WorkspaceCtx = Depends(workspace_ctx)):
    base = (select(Membership, User).join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == ctx.workspace.id))
    total = ctx.db.scalar(select(func.count()).select_from(base.subquery()))
    rows = ctx.db.execute(base.order_by(Membership.created_at).limit(page.limit).offset(page.offset)).all()
    return {"items": [member_out(m, u) for m, u in rows], "total": total}


def _can_assign(actor_role: str, role: str) -> bool:
    # owners may assign anything; admins only roles strictly below admin
    return actor_role == "owner" or RANK[role] < RANK["admin"]


@router.post("/v1/workspaces/{wid}/members", status_code=201)
def add_member(body: MemberIn, ctx: WorkspaceCtx = Depends(workspace_ctx)):
    ctx.require("admin")
    if not _can_assign(ctx.role, body.role):
        raise forbidden("only an owner can grant admin or owner")
    db = ctx.db
    target = db.scalar(select(User).where(User.email == body.email.lower()))
    if target is None:
        raise not_found("user")
    if db.scalar(select(Membership.id).where(Membership.workspace_id == ctx.workspace.id, Membership.user_id == target.id)):
        raise ApiError(409, "conflict", "user is already a member")
    n = db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == ctx.workspace.id))
    d = check_count_limit(db, ctx.workspace.id, "max_members", n)
    if not d.allowed:
        raise ApiError(403, "entitlement_denied", d.reason)
    m = Membership(workspace_id=ctx.workspace.id, user_id=target.id, role=body.role)
    db.add(m)
    audit.record(db, action="member.add", workspace_id=ctx.workspace.id, actor=ctx.user.id, target=target.id,
                 meta={"role": body.role})
    db.commit()
    return member_out(m, target)


@router.delete("/v1/workspaces/{wid}/members/{uid}", status_code=204)
def remove_member(uid: str, ctx: WorkspaceCtx = Depends(workspace_ctx)):
    db = ctx.db
    m = db.scalar(select(Membership).where(Membership.workspace_id == ctx.workspace.id, Membership.user_id == uid))
    if m is None:
        raise not_found("member")
    if uid != ctx.user.id:
        ctx.require("admin")
        if not _can_assign(ctx.role, m.role):
            raise forbidden("only an owner can remove an admin or owner")
    if m.role == "owner":
        owners = db.scalar(select(func.count()).select_from(Membership)
                           .where(Membership.workspace_id == ctx.workspace.id, Membership.role == "owner"))
        if owners <= 1:
            raise ApiError(409, "conflict", "cannot remove the last owner")
    db.delete(m)
    audit.record(db, action="member.remove", workspace_id=ctx.workspace.id, actor=ctx.user.id, target=uid)
    db.commit()


def _parse_bound(v: str | None, end: bool) -> datetime | None:
    if not v:
        return None
    try:
        if len(v) == 10:
            d = date.fromisoformat(v)
            return datetime.combine(d, time.max if end else time.min, tzinfo=timezone.utc)
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise ApiError(422, "invalid_request", "from/to must be ISO-8601 dates or timestamps") from None


@router.get("/v1/workspaces/{wid}/usage")
def usage(frm: str | None = Query(None, alias="from"), to: str | None = None, ctx: WorkspaceCtx = Depends(workspace_ctx)):
    lo, hi = _parse_bound(frm, False), _parse_bound(to, True)
    db = ctx.db
    conds = [UsageEvent.workspace_id == ctx.workspace.id]
    if lo:
        conds.append(UsageEvent.at >= lo)
    if hi:
        conds.append(UsageEvent.at <= hi)
    tot = db.execute(select(func.coalesce(func.sum(UsageEvent.input_tokens), 0), func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                            func.coalesce(func.sum(UsageEvent.usd), 0.0), func.count()).where(*conds)).one()
    by_run = db.execute(select(UsageEvent.run_id, UsageEvent.project_id, func.sum(UsageEvent.input_tokens),
                               func.sum(UsageEvent.output_tokens), func.sum(UsageEvent.usd))
                        .where(*conds).group_by(UsageEvent.run_id, UsageEvent.project_id)
                        .order_by(func.sum(UsageEvent.usd).desc()).limit(500)).all()
    by_model = db.execute(select(UsageEvent.model, func.sum(UsageEvent.input_tokens), func.sum(UsageEvent.output_tokens),
                                 func.sum(UsageEvent.usd)).where(*conds).group_by(UsageEvent.model)).all()
    return {"from": lo and lo.isoformat(), "to": hi and hi.isoformat(),
            "totals": {"input_tokens": int(tot[0]), "output_tokens": int(tot[1]), "usd": round(float(tot[2]), 6),
                       "events": tot[3]},
            "by_run": [{"run_id": r, "project_id": p, "input_tokens": int(i or 0), "output_tokens": int(o or 0),
                        "usd": round(float(u or 0), 6)} for r, p, i, o, u in by_run],
            "by_model": [{"model": m, "input_tokens": int(i or 0), "output_tokens": int(o or 0),
                          "usd": round(float(u or 0), 6)} for m, i, o, u in by_model]}


@router.get("/v1/workspaces/{wid}/entitlements")
def entitlements(ctx: WorkspaceCtx = Depends(workspace_ctx)):
    plan, limits = effective_limits(ctx.db, ctx.workspace.id)
    out = {"plan": plan, "limits": limits, "consumption": consumption(ctx.db, ctx.workspace.id)}
    ctx.db.commit()  # lazily created entitlement row
    return out


@router.get("/v1/workspaces/{wid}/audit")
def audit_log(page: Page = Depends(), action: str | None = None, ctx: WorkspaceCtx = Depends(workspace_ctx)):
    ctx.require("admin")
    conds = [AuditLog.workspace_id == ctx.workspace.id]
    if action:
        conds.append(AuditLog.action == action)
    total = ctx.db.scalar(select(func.count()).select_from(AuditLog).where(*conds))
    rows = ctx.db.scalars(select(AuditLog).where(*conds).order_by(AuditLog.at.desc(), AuditLog.id)
                          .limit(page.limit).offset(page.offset)).all()
    return {"items": [audit_out(a) for a in rows], "total": total}
