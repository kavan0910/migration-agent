"""THE tenancy boundary.

Every project-scoped read goes through ``scoped_select`` (membership join on the caller). Routes never build their own
workspace filter. A resource outside the caller's workspaces behaves exactly like a missing one (404). Insufficient
role inside a workspace the caller belongs to is 403.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session

from .deps import current_user, get_db
from .errors import ApiError, forbidden, not_found
from .models import Membership, Project, Run, User, Workspace

ROLES = ("viewer", "member", "admin", "owner")
RANK = {r: i for i, r in enumerate(ROLES)}


def has_role(role: str, minimum: str) -> bool:
    return RANK.get(role, -1) >= RANK[minimum]


def scoped_select(model, user_id: str) -> Select:
    """SELECT model, membership.role restricted to workspaces the user belongs to. Works for any model with
    ``workspace_id`` (Project, Run, ...)."""
    return (select(model, Membership.role)
            .join(Membership, and_(Membership.workspace_id == model.workspace_id, Membership.user_id == user_id)))


@dataclass
class WorkspaceCtx:
    user: User
    workspace: Workspace
    role: str
    db: Session

    def require(self, minimum: str) -> "WorkspaceCtx":
        if not has_role(self.role, minimum):
            raise forbidden(f"requires role {minimum} or higher")
        return self


@dataclass
class ProjectCtx:
    user: User
    project: Project
    role: str
    db: Session

    @property
    def workspace_id(self) -> str:
        return self.project.workspace_id

    def require(self, minimum: str) -> "ProjectCtx":
        if not has_role(self.role, minimum):
            raise forbidden(f"requires role {minimum} or higher")
        return self


@dataclass
class RunCtx:
    user: User
    run: Run
    project: Project
    role: str
    db: Session

    def require(self, minimum: str) -> "RunCtx":
        if not has_role(self.role, minimum):
            raise forbidden(f"requires role {minimum} or higher")
        return self


def workspace_ctx(wid: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> WorkspaceCtx:
    row = db.execute(select(Workspace, Membership.role)
                     .join(Membership, and_(Membership.workspace_id == Workspace.id, Membership.user_id == user.id))
                     .where(Workspace.id == wid)).first()
    if row is None:
        raise not_found("workspace")
    return WorkspaceCtx(user=user, workspace=row[0], role=row[1], db=db)


def project_ctx(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> ProjectCtx:
    row = db.execute(scoped_select(Project, user.id).where(Project.id == pid)).first()
    if row is None:
        raise not_found("project")
    return ProjectCtx(user=user, project=row[0], role=row[1], db=db)


def run_ctx(rid: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> RunCtx:
    row = db.execute(scoped_select(Run, user.id).where(Run.id == rid)).first()
    if row is None:
        raise not_found("run")
    run, role = row
    prow = db.execute(scoped_select(Project, user.id).where(Project.id == run.project_id)).first()
    if prow is None:  # pragma: no cover - FK guarantees the project exists
        raise not_found("run")
    return RunCtx(user=user, run=run, project=prow[0], role=role, db=db)


__all__ = ["ROLES", "ApiError", "scoped_select", "workspace_ctx", "project_ctx", "run_ctx", "has_role",
           "WorkspaceCtx", "ProjectCtx", "RunCtx"]
