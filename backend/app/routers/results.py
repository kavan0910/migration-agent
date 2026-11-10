from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import audit
from ..errors import ApiError, not_found
from ..models import Comment, Resolution
from ..serializers import Page, comment_out, resolution_out, run_out
from ..services import results as res
from ..services import runs as run_service
from ..tenancy import ProjectCtx, project_ctx

router = APIRouter()
REANALYZE_TRIGGERS = {"needs_reanalysis", "accepted_doc"}
STATUSES = ("accepted_doc", "accepted_code", "dismissed", "needs_reanalysis")


def _store(request: Request):
    return request.app.state.store


# ------------------------------------------------------------------------------------------------ graph
@router.get("/v1/projects/{pid}/graph")
def graph(request: Request, version: int | None = Query(None, ge=0), type: str | None = Query(None, max_length=40),
          q: str | None = Query(None, max_length=200), page: Page = Depends(), ctx: ProjectCtx = Depends(project_ctx)):
    snap = res.snapshot(_store(request), ctx.project, version)
    nodes = snap.get("nodes", [])
    if type:
        nodes = [n for n in nodes if n.get("type") == type]
    if q:
        needle = q.lower()
        nodes = [n for n in nodes if needle in json.dumps(n, default=str).lower()]
    total = len(nodes)
    window = nodes[page.offset: page.offset + page.limit]
    ids = {n["id"] for n in window}
    edges = [e for e in snap.get("edges", []) if e.get("source") in ids or e.get("target") in ids]
    return {"version": snap.get("version", 0), "items": window, "total": total, "edges": edges}


@router.get("/v1/projects/{pid}/graph/versions")
def graph_versions(request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    vs = [{**v, "node_count": v.get("node_count", v.get("nodes", 0)), "edge_count": v.get("edge_count", v.get("edges", 0))}
          for v in res.versions(_store(request), ctx.project)]
    return {"items": vs, "total": len(vs)}


@router.get("/v1/projects/{pid}/graph/diff")
def graph_diff(request: Request, a: int = Query(..., ge=0), b: int = Query(..., ge=0),
               ctx: ProjectCtx = Depends(project_ctx)):
    store = _store(request)
    return res.diff(res.snapshot(store, ctx.project, a), res.snapshot(store, ctx.project, b))


@router.get("/v1/projects/{pid}/graph/nodes/{nid}")
def graph_node(nid: str, request: Request, version: int | None = Query(None, ge=0),
               ctx: ProjectCtx = Depends(project_ctx)):
    snap = res.snapshot(_store(request), ctx.project, version)
    node = next((n for n in snap.get("nodes", []) if n.get("id") == nid), None)
    if node is None:
        raise not_found("node")
    edges = [e for e in snap.get("edges", []) if e.get("source") == nid or e.get("target") == nid]
    by_id = {n.get("id"): n for n in snap.get("nodes", [])}
    nb_ids = {x for e in edges for x in (e.get("source"), e.get("target"))} - {nid}
    return {"version": snap.get("version", 0), "node": node, "edges": edges,
            "neighbours": [by_id[i] for i in sorted(nb_ids) if i in by_id]}


@router.get("/v1/projects/{pid}/document")
def document(request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    t = res.read_text(_store(request), ctx.project, "document.md")
    if t is None:
        raise not_found("document")
    return PlainTextResponse(t, media_type="text/markdown; charset=utf-8")


@router.get("/v1/projects/{pid}/changelog")
def changelog(request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    t = res.read_text(_store(request), ctx.project, "changelog.md")
    if t is None:
        raise not_found("changelog")
    return PlainTextResponse(t, media_type="text/markdown; charset=utf-8")


@router.get("/v1/projects/{pid}/export")
def export(request: Request, format: Literal["md", "json", "html"] = Query("md"), ctx: ProjectCtx = Depends(project_ctx)):
    store, p = _store(request), ctx.project
    if format == "md":
        t = res.read_text(store, p, "document.md")
        if t is None:
            raise not_found("document")
        return Response(t, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="understanding.md"'})
    if format == "json":
        body = {"project": {"id": p.id, "name": p.name}, "graph": res.snapshot(store, p, None),
                "versions": res.versions(store, p), "findings": res.read_json(store, p, "review_state.json", {}).get("findings", {})}
        return Response(json.dumps(body), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="understanding.json"'})
    t = res.read_text(store, p, "graph.html")
    if t is None:
        raise not_found("html export")
    return Response(t, media_type="text/html; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="graph.html"',
        "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"})


# ------------------------------------------------------------------------------------------------ review
def _review_state(request: Request, ctx: ProjectCtx) -> dict:
    return res.read_json(_store(request), ctx.project, "review_state.json", {}) or {}


@router.get("/v1/projects/{pid}/findings")
def findings(request: Request, page: Page = Depends(), status: str | None = None, kind: str | None = None,
             ctx: ProjectCtx = Depends(project_ctx)):
    rs = _review_state(request, ctx)
    items = list((rs.get("findings") or {}).get("findings", []))
    engine_res: dict[str, dict] = {}
    for r in (rs.get("resolutions") or {}).get("resolutions", []):
        engine_res[r.get("finding_id")] = r
    db_res: dict[str, Resolution] = {}
    for r in ctx.db.scalars(select(Resolution).where(Resolution.project_id == ctx.project.id).order_by(Resolution.created_at)):
        db_res[r.finding_id] = r
    counts = dict(ctx.db.execute(select(Comment.item_id, func.count()).where(Comment.project_id == ctx.project.id)
                                 .group_by(Comment.item_id)).all())
    out = []
    for f in items:
        fid = f.get("id")
        cur = resolution_out(db_res[fid]) if fid in db_res else engine_res.get(fid)
        row = {**f, "resolution": cur, "comment_count": counts.get(fid, 0)}
        if status and (cur or {}).get("status") != status:
            continue
        if kind and f.get("kind") != kind:
            continue
        out.append(row)
    return {"items": out[page.offset: page.offset + page.limit], "total": len(out)}


class ResolutionIn(BaseModel):
    status: Literal["accepted_doc", "accepted_code", "dismissed", "needs_reanalysis"]
    note: str = Field(default="", max_length=5000)
    model_profile: Literal["fast", "balanced", "best"] = "balanced"
    max_usd: float | None = Field(default=None, gt=0)


@router.post("/v1/projects/{pid}/findings/{fid}/resolution", status_code=201)
def resolve(fid: str, body: ResolutionIn, request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    ctx.require("member")
    rs = _review_state(request, ctx)
    if fid not in {f.get("id") for f in (rs.get("findings") or {}).get("findings", [])}:
        raise not_found("finding")
    db, p = ctx.db, ctx.project
    r = Resolution(workspace_id=p.workspace_id, project_id=p.id, finding_id=fid, status=body.status, note=body.note,
                   author_id=ctx.user.id, author_name=ctx.user.display_name or ctx.user.email)
    db.add(r)
    audit.record(db, action="finding.resolve", workspace_id=p.workspace_id, actor=ctx.user.id, target=fid,
                 meta={"status": body.status, "project_id": p.id})
    run = None
    denied = None
    if body.status in REANALYZE_TRIGGERS:
        try:
            with db.begin_nested():
                run, _ = run_service.create_run(db, project=p, user_id=ctx.user.id, type_="reanalyze",
                                                profile=body.model_profile, max_usd=body.max_usd, incremental=True,
                                                idempotency_key=None, settings=request.app.state.settings, auto=True)
        except ApiError as e:
            if e.code == "run_in_progress":
                denied = None  # a queued reanalyze run will pick this resolution up
            else:
                denied = e.message
    db.commit()
    return {"resolution": resolution_out(r), "run": run_out(run) if run else None, "run_denied_reason": denied}


class CommentIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=5000)


@router.get("/v1/projects/{pid}/comments")
def list_comments(item_id: str | None = None, page: Page = Depends(), ctx: ProjectCtx = Depends(project_ctx)):
    cond = [Comment.project_id == ctx.project.id]
    if item_id:
        cond.append(Comment.item_id == item_id)
    total = ctx.db.scalar(select(func.count()).select_from(Comment).where(*cond))
    rows = ctx.db.scalars(select(Comment).where(*cond).order_by(Comment.created_at).limit(page.limit).offset(page.offset))
    return {"items": [comment_out(c) for c in rows], "total": total}


@router.post("/v1/projects/{pid}/comments", status_code=201)
def add_comment(body: CommentIn, request: Request, ctx: ProjectCtx = Depends(project_ctx)):
    ctx.require("member")
    rs = _review_state(request, ctx)
    # comments attach to graph/doc ids and to finding ids (the findings page threads discussion on the finding)
    commentable = set(rs.get("known_ids") or []) | {f.get("id") for f in (rs.get("findings") or {}).get("findings", [])}
    if body.item_id not in commentable:
        raise not_found("item")
    p = ctx.project
    c = Comment(workspace_id=p.workspace_id, project_id=p.id, item_id=body.item_id, text=body.text.strip(),
                author_id=ctx.user.id, author_name=ctx.user.display_name or ctx.user.email)
    ctx.db.add(c)
    ctx.db.commit()
    return comment_out(c)
