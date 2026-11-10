from __future__ import annotations

from datetime import datetime

from fastapi import Query

from .models import Artifact, AuditLog, Comment, Membership, Project, Resolution, Run, Source, User, UserDoc, Workspace


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


class Page:
    def __init__(self, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        self.limit, self.offset = limit, offset


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "created_at": iso(u.created_at)}


def workspace_out(w: Workspace, role: str | None = None) -> dict:
    d = {"id": w.id, "name": w.name, "created_at": iso(w.created_at)}
    if role:
        d["role"] = role
    return d


def member_out(m: Membership, u: User) -> dict:
    return {"user_id": u.id, "email": u.email, "display_name": u.display_name, "role": m.role,
            "created_at": iso(m.created_at)}


def source_out(s: Source | None) -> dict | None:
    if s is None:
        return None
    return {"id": s.id, "kind": s.kind, "git_url": s.git_url, "sha256": s.sha256, "bytes": s.bytes,
            "file_count": s.file_count, "created_at": iso(s.created_at)}


def project_out(p: Project, source: Source | None = None, last_run: Run | None = None) -> dict:
    return {"id": p.id, "workspace_id": p.workspace_id, "name": p.name, "description": p.description,
            "created_at": iso(p.created_at), "updated_at": iso(p.updated_at), "source": source_out(source),
            "estimate": p.estimate, "last_run": run_out(last_run) if last_run else None}


def user_doc_out(d: UserDoc) -> dict:
    return {"id": d.id, "filename": d.filename, "sha256": d.sha256, "bytes": d.bytes, "created_at": iso(d.created_at)}


def run_out(r: Run) -> dict:
    return {"id": r.id, "project_id": r.project_id, "workspace_id": r.workspace_id, "type": r.type, "status": r.status,
            "model_profile": r.model_profile, "max_usd": r.max_usd, "incremental": r.incremental,
            "cost_usd": round(r.cost_usd or 0.0, 6), "estimate_usd": r.estimate_usd, "engine_version": r.engine_version,
            "error": r.error, "current_stage": r.current_stage, "completed_stages": list(r.completed_stages or []),
            "cancel_requested": r.cancel_requested, "created_at": iso(r.created_at),
            "started_at": iso(r.started_at), "finished_at": iso(r.finished_at)}


def audit_out(a: AuditLog) -> dict:
    return {"id": a.id, "workspace_id": a.workspace_id, "actor": a.actor, "action": a.action, "target": a.target,
            "at": iso(a.at), "meta": a.meta}


def comment_out(c: Comment) -> dict:
    return {"id": c.id, "item_id": c.item_id, "text": c.text, "author": c.author_name, "author_id": c.author_id,
            "created_at": iso(c.created_at)}


def resolution_out(r: Resolution) -> dict:
    return {"id": r.id, "finding_id": r.finding_id, "status": r.status, "note": r.note, "author": r.author_name,
            "created_at": iso(r.created_at)}


def artifact_out(a: Artifact) -> dict:
    return {"id": a.id, "kind": a.kind, "sha256": a.sha256, "bytes": a.bytes, "run_id": a.run_id}
