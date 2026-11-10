from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..deps import client_ip, current_user, get_db
from ..errors import ApiError
from ..models import AuditLog, Membership, User, Workspace
from ..security import DevTokens, hash_password, verify_password
from ..serializers import Page, audit_out, user_out, workspace_out
from ..services import accounts

router = APIRouter()


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


def _dev_only(request: Request) -> DevTokens:
    v = request.app.state.verifier
    if not isinstance(v, DevTokens):
        raise ApiError(404, "not_found", "password authentication is disabled (AUTH_MODE=oidc)")
    return v


@router.post("/v1/auth/signup", status_code=201)
def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)):
    tokens = _dev_only(request)
    if not request.app.state.settings.signup_enabled:
        raise ApiError(403, "signup_disabled", "sign up is disabled")
    st = request.app.state.settings
    try:
        u = accounts.create_user(db, email=body.email, password_hash=hash_password(body.password),
                                 display_name=body.display_name, plan=st.default_plan,
                                 max_workspaces=st.max_workspaces_per_user)
        db.commit()
    except IntegrityError:
        # Trade-off (F7): a uniform response would need e-mail verification, which does not exist yet, so signup keeps
        # a 409 for an existing address. Compensating controls: signup is closed by default in production, the
        # endpoint is behind the per-IP auth rate limit, and the attempt is audited below.
        db.rollback()
        audit.record(db, action="auth.signup_conflict", workspace_id=None, actor=None,
                     target=hashlib.sha256(body.email.lower().encode()).hexdigest()[:16], meta={"ip": client_ip(request)})
        db.commit()
        raise ApiError(409, "conflict", "an account with this email already exists") from None
    tok, ttl = tokens.issue(u.id)
    return {"access_token": tok, "token_type": "bearer", "expires_in": ttl, "user": user_out(u)}


@router.post("/v1/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    tokens = _dev_only(request)
    throttle = request.app.state.login_throttle
    email = body.email.lower()
    wait = throttle.retry_after(email) if request.app.state.settings.rate_limit_enabled else 0.0
    if wait > 0:  # per-account exponential backoff (known or unknown e-mail alike)
        raise ApiError(429, "rate_limited", "too many failed sign-in attempts, try again later",
                       {"Retry-After": str(int(wait) + 1)})
    u = db.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, u.password_hash if u else None)
    if not ok or u is None or not u.is_active:
        throttle.failure(email)
        audit.record(db, action="auth.login_failed", workspace_id=None, actor=u.id if u else None,
                     target=u.id if u else hashlib.sha256(email.encode()).hexdigest()[:16],
                     meta={"ip": client_ip(request)})
        db.commit()
        raise ApiError(401, "unauthorized", "invalid email or password")
    throttle.success(email)
    audit.record(db, action="auth.login", workspace_id=None, actor=u.id, target=u.id, meta={"ip": client_ip(request)})
    db.commit()
    tok, ttl = tokens.issue(u.id)
    return {"access_token": tok, "token_type": "bearer", "expires_in": ttl, "user": user_out(u)}


@router.get("/v1/me/audit")
def my_auth_audit(page: Page = Depends(), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The caller's own account-level security events (sign-up, successful and failed sign-ins)."""
    conds = [AuditLog.actor == user.id, AuditLog.workspace_id.is_(None), AuditLog.action.like("auth.%")]
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*conds))
    rows = db.scalars(select(AuditLog).where(*conds).order_by(AuditLog.at.desc(), AuditLog.id)
                      .limit(page.limit).offset(page.offset)).all()
    return {"items": [audit_out(a) for a in rows], "total": total}


@router.get("/v1/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Workspace, Membership.role)
                      .join(Membership, Membership.workspace_id == Workspace.id)
                      .where(Membership.user_id == user.id).order_by(Workspace.created_at)).all()
    return {**user_out(user), "workspaces": [workspace_out(w, r) for w, r in rows]}
