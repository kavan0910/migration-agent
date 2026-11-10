from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..billing import ensure_entitlement, get_billing
from ..db import lock_row
from ..errors import ApiError
from ..models import Membership, User, Workspace
from ..security import Identity


def create_workspace(db: Session, name: str, owner: User, *, plan: str | None = None,
                     max_per_user: int | None = None) -> Workspace:
    """``max_per_user`` caps how many workspaces one user may create (denial-of-wallet: every workspace carries its own
    plan budget). The check runs under a lock on the user row so parallel requests cannot all pass."""
    if max_per_user is None:
        from ..config import get_settings
        max_per_user = get_settings().max_workspaces_per_user
    lock_row(db, User, owner.id)
    n = db.scalar(select(func.count()).select_from(Workspace).where(Workspace.created_by == owner.id)) or 0
    if n >= max_per_user:
        raise ApiError(403, "entitlement_denied", f"workspace limit reached ({max_per_user} per account)")
    ws = Workspace(name=name.strip()[:200] or "Workspace", created_by=owner.id)
    db.add(ws)
    db.flush()
    db.add(Membership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    ensure_entitlement(db, ws.id, plan)
    try:
        get_billing().ensure_customer(ws.id, ws.name)
    except Exception:  # the seam must never break workspace creation
        pass
    audit.record(db, action="workspace.create", workspace_id=ws.id, actor=owner.id, target=ws.id, meta={"name": ws.name})
    return ws


def create_user(db: Session, *, email: str, password_hash: str | None, display_name: str = "",
                external_id: str | None = None, plan: str | None = None, max_workspaces: int | None = None) -> User:
    u = User(email=email.lower(), password_hash=password_hash, display_name=display_name[:200] or email.split("@")[0],
             external_id=external_id)
    db.add(u)
    db.flush()
    create_workspace(db, f"{u.display_name}'s workspace", u, plan=plan, max_per_user=max_workspaces)
    audit.record(db, action="auth.signup", workspace_id=None, actor=u.id, target=u.id)
    return u


def create_oidc_user(db: Session, ident: Identity, *, plan: str | None = None, max_workspaces: int | None = None) -> User:
    email = (ident.email or "").lower() if ident.email_verified and ident.email else ""
    if not email or db.scalar(select(User.id).where(User.email == email)):
        email = f"{hashlib.sha256(ident.external_id().encode()).hexdigest()[:40]}@oidc.invalid"
        if db.scalar(select(User.id).where(User.email == email)):  # pragma: no cover
            raise ValueError("duplicate identity")
    return create_user(db, email=email, password_hash=None, display_name=ident.name or email.split("@")[0],
                       external_id=ident.external_id(), plan=plan, max_workspaces=max_workspaces)
