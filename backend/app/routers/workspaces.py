from __future__ import annotations

import hashlib
import secrets
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..billing import check_count_limit, consumption, effective_limits
from ..db import lock_row
from ..deps import current_user, get_db
from ..errors import ApiError, forbidden, not_found
from ..models import AuditLog, Invitation, Membership, Project, Run, UsageEvent, User, Workspace
from ..serializers import Page, audit_out, iso, member_out, workspace_out
from ..services import accounts
from ..tenancy import RANK, WorkspaceCtx, workspace_ctx

router = APIRouter()
Role = Literal["owner", "admin", "member", "viewer"]


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class InviteIn(BaseModel):
    email: EmailStr
    role: Role = "member"


class AcceptIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.get("/v1/workspaces")
def list_workspaces(page: Page = Depends(), user: User = Depends(current_user), db: Session = Depends(get_db)):
    base = (select(Workspace, Membership.role).join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.execute(base.order_by(Workspace.created_at).limit(page.limit).offset(page.offset)).all()
    return {"items": [workspace_out(w, r) for w, r in rows], "total": total}


@router.post("/v1/workspaces", status_code=201)
def create_workspace(body: WorkspaceIn, request: Request, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    st = request.app.state.settings
    ws = accounts.create_workspace(db, body.name, user, plan=st.default_plan, max_per_user=st.max_workspaces_per_user)
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


def _hash_token(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _invite_out(i: Invitation) -> dict:
    return {"id": i.id, "email": i.email, "role": i.role, "expires_at": iso(i.expires_at), "created_at": iso(i.created_at)}


@router.post("/v1/workspaces/{wid}/invitations", status_code=202)
def invite(body: InviteIn, request: Request, ctx: WorkspaceCtx = Depends(workspace_ctx)):
    """Create a pending invitation. The response is identical whether or not the e-mail belongs to a registered user
    or is already a member (no account-existence oracle). The one-time token is returned to the inviting admin to pass
    to the invitee out of band; only its hash is stored. The invitee must be signed in as that e-mail to accept."""
    ctx.require("admin")
    if not _can_assign(ctx.role, body.role):
        raise forbidden("only an owner can grant admin or owner")
    st, db = request.app.state.settings, ctx.db
    lock_row(db, Workspace, ctx.workspace.id)
    now = datetime.now(timezone.utc)
    pending = db.scalar(select(func.count()).select_from(Invitation)
                        .where(Invitation.workspace_id == ctx.workspace.id, Invitation.accepted_at.is_(None),
                               Invitation.expires_at > now)) or 0
    if pending >= st.max_pending_invites:
        raise ApiError(429, "rate_limited", "too many pending invitations")
    email = body.email.lower()
    tok = secrets.token_urlsafe(32)
    inv = Invitation(workspace_id=ctx.workspace.id, email=email, role=body.role, token_hash=_hash_token(tok),
                     invited_by=ctx.user.id, expires_at=now + timedelta(hours=st.invite_ttl_hours))
    db.add(inv)
    audit.record(db, action="member.invite", workspace_id=ctx.workspace.id, actor=ctx.user.id,
                 target=hashlib.sha256(email.encode()).hexdigest()[:16], meta={"role": body.role})
    db.commit()
    return {"status": "invited", "invitation": _invite_out(inv), "token": tok}


@router.get("/v1/workspaces/{wid}/invitations")
def list_invitations(ctx: WorkspaceCtx = Depends(workspace_ctx)):
    ctx.require("admin")
    rows = ctx.db.scalars(select(Invitation).where(Invitation.workspace_id == ctx.workspace.id,
                                                   Invitation.accepted_at.is_(None),
                                                   Invitation.expires_at > datetime.now(timezone.utc))
                          .order_by(Invitation.created_at)).all()
    return {"items": [_invite_out(i) for i in rows], "total": len(rows)}


@router.delete("/v1/workspaces/{wid}/invitations/{iid}", status_code=204)
def revoke_invitation(iid: str, ctx: WorkspaceCtx = Depends(workspace_ctx)):
    ctx.require("admin")
    inv = ctx.db.scalar(select(Invitation).where(Invitation.id == iid, Invitation.workspace_id == ctx.workspace.id))
    if inv is None:
        raise not_found("invitation")
    ctx.db.delete(inv)
    audit.record(ctx.db, action="member.invite_revoke", workspace_id=ctx.workspace.id, actor=ctx.user.id, target=iid)
    ctx.db.commit()


@router.post("/v1/invitations/accept")
def accept_invitation(body: AcceptIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Accept as the authenticated invitee. Unknown, expired, used and wrong-account tokens all get the same 404."""
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == _hash_token(body.token)))
    now = datetime.now(timezone.utc)
    if (inv is None or inv.accepted_at is not None or inv.expires_at <= now or inv.email != user.email.lower()):
        raise not_found("invitation")
    lock_row(db, Workspace, inv.workspace_id)
    inv = db.scalar(select(Invitation).where(Invitation.id == inv.id))
    if inv is None or inv.accepted_at is not None:
        raise not_found("invitation")
    if db.scalar(select(Membership.id).where(Membership.workspace_id == inv.workspace_id, Membership.user_id == user.id)):
        raise ApiError(409, "conflict", "already a member of this workspace")
    n = db.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == inv.workspace_id))
    d = check_count_limit(db, inv.workspace_id, "max_members", n)
    if not d.allowed:
        raise ApiError(403, "entitlement_denied", d.reason)
    m = Membership(workspace_id=inv.workspace_id, user_id=user.id, role=inv.role)
    inv.accepted_at = now
    db.add(m)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "conflict", "already a member of this workspace") from None
    audit.record(db, action="member.add", workspace_id=inv.workspace_id, actor=user.id, target=user.id,
                 meta={"role": inv.role, "via": "invitation", "invited_by": inv.invited_by})
    db.commit()
    return member_out(m, user)


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
        lock_row(db, Workspace, ctx.workspace.id)  # serialise concurrent owner removals / self-leaves
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
                               func.sum(UsageEvent.output_tokens), func.sum(UsageEvent.usd), func.max(UsageEvent.at))
                        .where(*conds).group_by(UsageEvent.run_id, UsageEvent.project_id)
                        .order_by(func.sum(UsageEvent.usd).desc()).limit(500)).all()
    run_types = dict(db.execute(select(Run.id, Run.type).where(Run.workspace_id == ctx.workspace.id)).all())
    proj_names = dict(db.execute(select(Project.id, Project.name).where(Project.workspace_id == ctx.workspace.id)).all())
    by_model = db.execute(select(UsageEvent.model, func.sum(UsageEvent.input_tokens), func.sum(UsageEvent.output_tokens),
                                 func.sum(UsageEvent.usd)).where(*conds).group_by(UsageEvent.model)).all()
    return {"from": lo and lo.isoformat(), "to": hi and hi.isoformat(),
            "totals": {"input_tokens": int(tot[0]), "output_tokens": int(tot[1]), "usd": round(float(tot[2]), 6),
                       "events": tot[3], "runs": len(by_run)},
            "by_run": [{"run_id": r, "project_id": p, "project_name": proj_names.get(p), "type": run_types.get(r, "analyze"),
                        "input_tokens": int(i or 0), "output_tokens": int(o or 0),
                        "usd": round(float(u or 0), 6), "at": iso(at)} for r, p, i, o, u, at in by_run],
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
