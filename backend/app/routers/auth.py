from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..deps import current_user, get_db
from ..errors import ApiError
from ..models import Membership, User, Workspace
from ..security import DevTokens, hash_password, verify_password
from ..serializers import user_out, workspace_out
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
    try:
        u = accounts.create_user(db, email=body.email, password_hash=hash_password(body.password),
                                 display_name=body.display_name)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "conflict", "an account with this email already exists") from None
    tok, ttl = tokens.issue(u.id)
    return {"access_token": tok, "token_type": "bearer", "expires_in": ttl, "user": user_out(u)}


@router.post("/v1/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    tokens = _dev_only(request)
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    ok = verify_password(body.password, u.password_hash if u else None)
    if not ok or u is None or not u.is_active:
        raise ApiError(401, "unauthorized", "invalid email or password")
    audit.record(db, action="auth.login", workspace_id=None, actor=u.id, target=u.id)
    db.commit()
    tok, ttl = tokens.issue(u.id)
    return {"access_token": tok, "token_type": "bearer", "expires_in": ttl, "user": user_out(u)}


@router.get("/v1/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Workspace, Membership.role)
                      .join(Membership, Membership.workspace_id == Workspace.id)
                      .where(Membership.user_id == user.id).order_by(Workspace.created_at)).all()
    return {**user_out(user), "workspaces": [workspace_out(w, r) for w, r in rows]}
