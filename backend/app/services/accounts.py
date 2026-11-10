from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..billing import ensure_entitlement, get_billing
from ..models import Membership, User, Workspace
from ..security import Identity


def create_workspace(db: Session, name: str, owner: User) -> Workspace:
    ws = Workspace(name=name.strip()[:200] or "Workspace", created_by=owner.id)
    db.add(ws)
    db.flush()
    db.add(Membership(workspace_id=ws.id, user_id=owner.id, role="owner"))
    ensure_entitlement(db, ws.id)
    try:
        get_billing().ensure_customer(ws.id, ws.name)
    except Exception:  # the seam must never break workspace creation
        pass
    audit.record(db, action="workspace.create", workspace_id=ws.id, actor=owner.id, target=ws.id, meta={"name": ws.name})
    return ws


def create_user(db: Session, *, email: str, password_hash: str | None, display_name: str = "",
                external_id: str | None = None) -> User:
    u = User(email=email.lower(), password_hash=password_hash, display_name=display_name[:200] or email.split("@")[0],
             external_id=external_id)
    db.add(u)
    db.flush()
    create_workspace(db, f"{u.display_name}'s workspace", u)
    audit.record(db, action="auth.signup", workspace_id=None, actor=u.id, target=u.id)
    return u


def create_oidc_user(db: Session, ident: Identity) -> User:
    email = (ident.email or "").lower() if ident.email_verified and ident.email else ""
    if not email or db.scalar(select(User.id).where(User.email == email)):
        email = f"{ident.subject}@oidc.invalid"[:320].lower()
        if db.scalar(select(User.id).where(User.email == email)):  # pragma: no cover
            raise ValueError("duplicate identity")
    return create_user(db, email=email, password_hash=None, display_name=ident.name or email.split("@")[0],
                       external_id=ident.subject)
