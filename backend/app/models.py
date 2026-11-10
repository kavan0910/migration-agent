"""SQLAlchemy 2.x models (platform contract section 4).

Every project-scoped table carries a denormalised ``workspace_id`` so that Postgres row-level security can key on it.
Ids are UUID strings (portable between SQLite and Postgres).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
                        text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

Json = JSON().with_variant(JSONB(), "postgresql")


def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


from sqlalchemy.types import TypeDecorator  # noqa: E402


class TZDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc) if value is not None else None

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def _id() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=uid)


def _created() -> Mapped[datetime]:
    return mapped_column(TZDateTime, default=now, nullable=False)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _id()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(300), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(300), unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = _created()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    estimate: Mapped[dict | None] = mapped_column(Json, nullable=True)
    active_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # per-project execution lock
    ingested_source_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # source baked into stored state
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=now, onupdate=now)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # upload | git
    git_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    object_key: Mapped[str | None] = mapped_column(String(600), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _created()


class UserDoc(Base):
    __tablename__ = "user_docs"
    __table_args__ = (UniqueConstraint("project_id", "filename"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    object_key: Mapped[str] = mapped_column(String(600))
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(BigInteger)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _created()


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key"),
        # At most one queued/running run per (project, type): the database, not a check-then-insert, enforces it.
        Index("uq_runs_active_project_type", "project_id", "type", unique=True,
              sqlite_where=text("status IN ('queued','running')"),
              postgresql_where=text("status IN ('queued','running')")),
    )
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    model_profile: Mapped[str] = mapped_column(String(20))
    max_usd: Mapped[float] = mapped_column(Float)
    incremental: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    estimate_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    current_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    completed_stages: Mapped[list] = mapped_column(Json, default=list)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(Json, default=dict)
    at: Mapped[datetime] = _created()


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30))
    key: Mapped[str] = mapped_column(String(600))
    sha256: Mapped[str] = mapped_column(String(64))
    bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _created()


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_ws_at", "workspace_id", "at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # NULLed on project deletion
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)      # NULLed on project deletion
    stage: Mapped[str] = mapped_column(String(20), default="")
    task: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    at: Mapped[datetime] = _created()


class Plan(Base):
    __tablename__ = "plans"
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    limits: Mapped[dict] = mapped_column(Json, default=dict)


class Entitlement(Base):
    __tablename__ = "entitlements"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    plan: Mapped[str] = mapped_column(ForeignKey("plans.name"))
    overrides: Mapped[dict] = mapped_column(Json, default=dict)


class AuditLog(Base):
    """Append-only. No FK to projects/users so that deleting them never touches the trail."""
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_ws_at", "workspace_id", "at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(120), default="")
    at: Mapped[datetime] = _created()
    meta: Mapped[dict] = mapped_column(Json, default=dict)


class DeletionTask(Base):
    """Durable tombstone: object-store prefix that must be removed. Written in the SAME transaction as the row deletion
    (or by the orphan reconciler) and retried by the worker's reaper until the store confirms removal."""
    __tablename__ = "deletion_tasks"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # no FK: the project row is gone
    prefix: Mapped[str] = mapped_column(String(300), unique=True)
    reason: Mapped[str] = mapped_column(String(30), default="project.delete")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_try_at: Mapped[datetime] = mapped_column(TZDateTime, default=now)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()


class Invitation(Base):
    """Pending workspace invitation. Only the SHA-256 of the token is stored."""
    __tablename__ = "invitations"
    __table_args__ = (Index("ix_invitations_ws", "workspace_id"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = _created()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "available_at"),)
    id: Mapped[str] = _id()
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued running done failed cancelled
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(TZDateTime, default=now)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column(String(100))
    text: Mapped[str] = mapped_column(Text)
    author_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_name: Mapped[str] = mapped_column(String(200), default="")
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


class Resolution(Base):
    __tablename__ = "resolutions"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_name: Mapped[str] = mapped_column(String(200), default="")
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()


# Tables that carry workspace_id and are covered by Postgres RLS policies (see migrations).
RLS_TABLES = ["projects", "sources", "user_docs", "runs", "run_events", "artifacts", "usage_events", "jobs",
              "comments", "resolutions"]
