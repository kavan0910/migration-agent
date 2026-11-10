from __future__ import annotations

import hashlib
import uuid
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, engine as engine_mod
from ..billing import can_start_run, check_count_limit, effective_limits
from ..errors import ApiError, not_found
from ..materialize import (latest_source, limits_from, materialize_for_estimate)
from ..models import Project, Resolution, Run, Source, UserDoc
from ..profiles import PROFILES, engine_config
from ..safety import UnsafeInput, extract_archive, sanitize_filename, validate_git_url
from ..serializers import Page, project_out, source_out, user_doc_out
from ..services import projects as project_service
from ..storage import project_prefix
from ..tenancy import ProjectCtx, WorkspaceCtx, project_ctx, workspace_ctx
from ..services.runs import ACTIVE

router = APIRouter()
_estimate_slots = threading.BoundedSemaphore(2)
DOC_EXTS = {".md", ".markdown", ".txt", ".rst", ".adoc", ".html", ".htm", ".pdf", ".docx", ".json", ".yaml", ".yml"}


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class EstimateIn(BaseModel):
    model_profile: str = "balanced"
    max_usd: float | None = Field(default=None, gt=0)
    incremental: bool = False


def _detail(db: Session, p: Project, store=None) -> dict:
    last = db.scalar(select(Run).where(Run.project_id == p.id).order_by(Run.created_at.desc()).limit(1))
    nodes = open_f = None
    if store is not None:
        from ..services import results as res
        vs = res.versions(store, p)
        if vs:
            nodes = int(vs[-1].get("node_count", vs[-1].get("nodes", 0)) or 0)
            rs = res.read_json(store, p, "review_state.json", {}) or {}
            fl = (rs.get("findings") or {}).get("findings", [])
            done = {r.finding_id for r in db.scalars(select(Resolution).where(Resolution.project_id == p.id))}
            done |= {r.get("finding_id") for r in (rs.get("resolutions") or {}).get("resolutions", [])}
            open_f = sum(1 for f in fl if f.get("id") not in done)
    return project_out(p, latest_source(db, p.id), last, nodes, open_f)


@router.post("/v1/workspaces/{wid}/projects", status_code=201)
def create_project(body: ProjectIn, ctx: WorkspaceCtx = Depends(workspace_ctx)):
    ctx.require("member")
    db = ctx.db
    n = db.scalar(select(func.count()).select_from(Project).where(Project.workspace_id == ctx.workspace.id))
    d = check_count_limit(db, ctx.workspace.id, "max_projects", n)
    if not d.allowed:
        raise ApiError(403, "entitlement_denied", d.reason)
    p = Project(workspace_id=ctx.workspace.id, name=body.name.strip(), description=body.description, created_by=ctx.user.id)
    db.add(p)
    db.flush()
    audit.record(db, action="project.create", workspace_id=p.workspace_id, actor=ctx.user.id, target=p.id)
    db.commit()
    return project_out(p)


@router.get("/v1/workspaces/{wid}/projects")
def list_projects(request: Request, page: Page = Depends(), ctx: WorkspaceCtx = Depends(workspace_ctx)):
    db = ctx.db
    cond = Project.workspace_id == ctx.workspace.id
    total = db.scalar(select(func.count()).select_from(Project).where(cond))
    rows = db.scalars(select(Project).where(cond).order_by(Project.created_at.desc(), Project.id)
                      .limit(page.limit).offset(page.offset)).all()
    return {"items": [_detail(db, p, request.app.state.store) for p in rows], "total": total}


@router.get("/v1/projects/{pid}")
def get_project(request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    return _detail(ctx.db, ctx.project, request.app.state.store)


@router.patch("/v1/projects/{pid}")
def patch_project(body: ProjectPatch, ctx: ProjectCtx = Depends(project_ctx)):
    ctx.require("admin")
    p = ctx.project
    if body.name is not None:
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description
    audit.record(ctx.db, action="project.update", workspace_id=p.workspace_id, actor=ctx.user.id, target=p.id)
    ctx.db.commit()
    return _detail(ctx.db, p)


@router.delete("/v1/projects/{pid}", status_code=204)
def delete_project(request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    ctx.require("admin")
    project_service.delete_project(ctx.db, request.app.state.store, ctx.project, ctx.user.id)


# ------------------------------------------------------------------------------------------------ source
class GitIn(BaseModel):
    git_url: str = Field(max_length=2000)


async def _spool_upload(upload, max_bytes: int, suffix: str = "") -> tuple[Path, int, str]:
    fd, name = tempfile.mkstemp(prefix="ma-upload-", suffix=suffix)
    h, n = hashlib.sha256(), 0
    try:
        with open(fd, "wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                n += len(chunk)
                if n > max_bytes:
                    raise ApiError(413, "payload_too_large", f"file exceeds {max_bytes // (1024 * 1024)} MB")
                h.update(chunk)
                out.write(chunk)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name), n, h.hexdigest()


@router.post("/v1/projects/{pid}/source", status_code=201)
async def add_source(request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    ctx.require("member")
    st = request.app.state.settings
    ctype = request.headers.get("content-type", "").lower()
    if ctype.startswith("application/json"):
        try:
            body = GitIn.model_validate(await request.json())
        except Exception:
            raise ApiError(422, "invalid_request", 'expected {"git_url": "https://..."}') from None
        return await run_in_threadpool(_add_git, request, ctx, body.git_url)
    if not ctype.startswith("multipart/form-data"):
        raise ApiError(422, "invalid_request", "send multipart/form-data (file) or JSON {git_url}")
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "read"):
        raise ApiError(422, "invalid_request", "multipart field 'file' is required")
    _, limits = effective_limits(ctx.db, ctx.workspace_id)
    cap_mb = min(st.max_upload_mb, limits.get("max_source_mb") or st.max_upload_mb)
    tmp, size, sha = await _spool_upload(up, cap_mb * 1024 * 1024)
    try:
        return await run_in_threadpool(_add_upload, request, ctx, tmp, size, sha)
    finally:
        tmp.unlink(missing_ok=True)


def _replace_source(request: Request, ctx: ProjectCtx, new: Source) -> None:
    db, store = ctx.db, request.app.state.store
    for old in db.scalars(select(Source).where(Source.project_id == ctx.project.id)):
        if old.object_key:
            store.delete(old.object_key)
        db.delete(old)
    db.add(new)
    db.flush()


def _add_upload(request: Request, ctx: ProjectCtx, tmp: Path, size: int, sha: str):
    st, store = request.app.state.settings, request.app.state.store
    try:
        stats = extract_archive(tmp, None, limits_from(st))  # validate only: nothing is written
    except UnsafeInput as e:
        raise ApiError(422, "invalid_source", str(e)) from None
    with open(tmp, "rb") as f:
        kind = "zip" if f.read(2) == b"PK" else "tar.gz"
    p = ctx.project
    src = Source(workspace_id=p.workspace_id, project_id=p.id, kind="upload", sha256=sha, bytes=size,
                 file_count=stats.files, created_by=ctx.user.id)
    src.id = str(uuid.uuid4())
    src.object_key = project_prefix(p.workspace_id, p.id) + f"source/{src.id}.{kind}"
    store.put_file(src.object_key, tmp)
    _replace_source(request, ctx, src)
    p.estimate = None
    audit.record(ctx.db, action="source.upload", workspace_id=p.workspace_id, actor=ctx.user.id, target=p.id,
                 meta={"bytes": size, "files": stats.files})
    ctx.db.commit()
    return source_out(src)


def _add_git(request: Request, ctx: ProjectCtx, url: str):
    st = request.app.state.settings
    try:
        safe = validate_git_url(url, allowed_hosts=st.allowed_git_hosts, resolver=request.app.state.resolver)
    except UnsafeInput as e:
        raise ApiError(422, "invalid_source", str(e)) from None
    p = ctx.project
    src = Source(workspace_id=p.workspace_id, project_id=p.id, kind="git", git_url=safe.url, created_by=ctx.user.id)
    _replace_source(request, ctx, src)
    p.estimate = None
    audit.record(ctx.db, action="source.git", workspace_id=p.workspace_id, actor=ctx.user.id, target=p.id,
                 meta={"host": safe.host})
    ctx.db.commit()
    return source_out(src)


@router.get("/v1/projects/{pid}/source")
def get_source(ctx: ProjectCtx = Depends(project_ctx)):
    s = latest_source(ctx.db, ctx.project.id)
    if s is None:
        raise not_found("source")
    return source_out(s)


# ------------------------------------------------------------------------------------------------ user docs
@router.post("/v1/projects/{pid}/user-docs", status_code=201)
async def add_user_doc(request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    ctx.require("member")
    st = request.app.state.settings
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise ApiError(422, "invalid_request", "send multipart/form-data with field 'file'")
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "read"):
        raise ApiError(422, "invalid_request", "multipart field 'file' is required")
    try:
        name = sanitize_filename(getattr(up, "filename", "") or "")
    except UnsafeInput as e:
        raise ApiError(422, "invalid_request", str(e)) from None
    if Path(name).suffix.lower() not in DOC_EXTS:
        raise ApiError(422, "invalid_request", "unsupported document type")
    tmp, size, sha = await _spool_upload(up, st.max_user_doc_mb * 1024 * 1024)
    try:
        return await run_in_threadpool(_store_doc, request, ctx, name, tmp, size, sha)
    finally:
        tmp.unlink(missing_ok=True)


def _store_doc(request: Request, ctx: ProjectCtx, name: str, tmp: Path, size: int, sha: str):
    st, store, db, p = request.app.state.settings, request.app.state.store, ctx.db, ctx.project
    if size == 0:
        raise ApiError(422, "invalid_request", "empty file")
    n = db.scalar(select(func.count()).select_from(UserDoc).where(UserDoc.project_id == p.id))
    if n >= st.max_user_docs:
        raise ApiError(409, "conflict", "too many documents")
    if db.scalar(select(UserDoc.id).where(UserDoc.project_id == p.id, UserDoc.filename == name)):
        raise ApiError(409, "conflict", "a document with this name already exists")
    doc = UserDoc(workspace_id=p.workspace_id, project_id=p.id, filename=name, sha256=sha, bytes=size,
                  object_key="", created_by=ctx.user.id)
    db.add(doc)
    db.flush()
    doc.object_key = project_prefix(p.workspace_id, p.id) + f"user_docs/{doc.id}/{name}"
    store.put_file(doc.object_key, tmp)
    audit.record(db, action="user_doc.add", workspace_id=p.workspace_id, actor=ctx.user.id, target=doc.id,
                 meta={"bytes": size})
    db.commit()
    return user_doc_out(doc)


@router.get("/v1/projects/{pid}/user-docs")
def list_user_docs(page: Page = Depends(), ctx: ProjectCtx = Depends(project_ctx)):
    cond = UserDoc.project_id == ctx.project.id
    total = ctx.db.scalar(select(func.count()).select_from(UserDoc).where(cond))
    rows = ctx.db.scalars(select(UserDoc).where(cond).order_by(UserDoc.created_at).limit(page.limit).offset(page.offset))
    return {"items": [user_doc_out(d) for d in rows], "total": total}


@router.delete("/v1/projects/{pid}/user-docs/{doc_id}", status_code=204)
def delete_user_doc(doc_id: str, request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    ctx.require("member")
    d = ctx.db.scalar(select(UserDoc).where(UserDoc.id == doc_id, UserDoc.project_id == ctx.project.id))
    if d is None:
        raise not_found("document")
    request.app.state.store.delete(d.object_key)
    ctx.db.delete(d)
    audit.record(ctx.db, action="user_doc.delete", workspace_id=ctx.workspace_id, actor=ctx.user.id, target=doc_id)
    ctx.db.commit()


# ------------------------------------------------------------------------------------------------ estimate
@router.post("/v1/projects/{pid}/estimate")
async def estimate(body: EstimateIn, request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    ctx.require("member")
    if body.model_profile not in PROFILES:
        raise ApiError(422, "invalid_request", f"model_profile must be one of {', '.join(PROFILES)}")
    if latest_source(ctx.db, ctx.project.id) is None:
        raise ApiError(409, "no_source", "upload a source archive or connect a git url first")
    if not _estimate_slots.acquire(blocking=False):
        raise ApiError(429, "rate_limited", "too many estimates in progress, retry shortly", {"Retry-After": "5"})
    try:
        return await run_in_threadpool(_estimate, request, ctx, body)
    finally:
        _estimate_slots.release()


def _estimate(request: Request, ctx: ProjectCtx, body: EstimateIn):
    st, db, p = request.app.state.settings, ctx.db, ctx.project
    eng = engine_mod.get_engine()
    cfg = engine_config(body.model_profile, st)
    cfg.pop("api_key", None)  # a dry run never needs the credential
    try:
        res = materialize_for_estimate(db, request.app.state.store, eng, st, p, cfg, body.incremental)
    except UnsafeInput as e:
        raise ApiError(422, "invalid_source", str(e)) from None
    except Exception as e:
        raise ApiError(502, "engine_error", f"estimate failed ({type(e).__name__})") from None
    budget = body.max_usd if body.max_usd is not None else st.default_max_usd
    dec = can_start_run(db, p.workspace_id, res, budget)
    p.estimate = {**res, "profile": body.model_profile, "incremental": body.incremental,
                  "at": datetime.now(timezone.utc).isoformat()}
    db.commit()
    return {"chunks": res["chunks"], "usd": res["usd"], "allowed": dec.allowed, "reason": dec.reason}


__all__ = ["ACTIVE"]
