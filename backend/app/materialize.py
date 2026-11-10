"""Turn stored objects into a local project directory (and back). Shared by the worker and the estimate endpoint.

Layout under ``{workspace_id}/{project_id}/``:
  source/{source_id}.{zip|tar.gz}   raw upload (or tarball of a git working tree, written on first use)
  user_docs/{doc_id}/{filename}
  state/project.tar.gz              engine project folder (pack_project), the resumable state
  results/...                       derived artifacts served by the API (document, graph versions, review state)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Artifact, Project, Source, UserDoc
from .safety import ArchiveLimits, extract_archive
from .storage import ObjectStore, StoreError, project_prefix, sha256_hex


def limits_from(s: Settings) -> ArchiveLimits:
    return ArchiveLimits(max_bytes=s.max_extracted_mb * 1024 * 1024, max_file_bytes=s.max_file_mb * 1024 * 1024,
                         max_files=s.max_archive_files, max_ratio=s.max_compression_ratio)


@contextmanager
def scratch_dir(settings: Settings, prefix: str = "ma-job-") -> Iterator[Path]:
    """Always removed, even on error."""
    base = settings.scratch_dir
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix=prefix, dir=base or None))
    os.chmod(d, 0o700)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def state_key(project: Project) -> str:
    return project_prefix(project.workspace_id, project.id) + "state/project.tar.gz"


def results_key(project: Project, name: str) -> str:
    return project_prefix(project.workspace_id, project.id) + "results/" + name


def latest_source(db: Session, project_id: str) -> Source | None:
    return db.scalar(select(Source).where(Source.project_id == project_id).order_by(Source.created_at.desc()))


def source_input(db: Session, store: ObjectStore, settings: Settings, project: Project, source: Source,
                 scratch: Path, git_clone=None) -> Path:
    """Extract (or clone) the source into ``scratch/src`` and return that directory."""
    dest = scratch / "src"
    if source.kind == "git" and not source.object_key:
        from .gitfetch import clone_public_repo
        clone = git_clone or clone_public_repo
        clone(source.git_url, dest, settings)
        tar_path = scratch / "git-source.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(dest, arcname=".", recursive=True)
        key = project_prefix(project.workspace_id, project.id) + f"source/{source.id}.tar.gz"
        store.put_file(key, tar_path)
        source.object_key = key
        source.sha256 = hashlib.sha256(tar_path.read_bytes()).hexdigest()
        source.bytes = tar_path.stat().st_size
        tar_path.unlink(missing_ok=True)
        db.commit()
        return dest
    data = scratch / "source-archive"
    data.write_bytes(store.get(source.object_key))
    try:
        extract_archive(data, dest, limits_from(settings))
    finally:
        data.unlink(missing_ok=True)
    return dest


def write_user_docs(db: Session, store: ObjectStore, project: Project, project_dir: Path) -> int:
    d = project_dir / "user_docs"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    docs = db.scalars(select(UserDoc).where(UserDoc.project_id == project.id)).all()
    for doc in docs:
        (d / doc.filename).write_bytes(store.get(doc.object_key))
    return len(docs)


def has_state(store: ObjectStore, project: Project) -> bool:
    return store.exists(state_key(project))


def unpack_state(store: ObjectStore, engine, project: Project, project_dir: Path) -> None:
    engine.unpack_project(store.get(state_key(project)), project_dir)


def put_artifact(db: Session, store: ObjectStore, project: Project, run_id: str | None, kind: str, key: str,
                 data: bytes) -> None:
    store.put(key, data)
    for old in db.scalars(select(Artifact).where(Artifact.project_id == project.id, Artifact.key == key)):
        db.delete(old)
    db.add(Artifact(workspace_id=project.workspace_id, project_id=project.id, run_id=run_id, kind=kind, key=key,
                    sha256=sha256_hex(data), bytes=len(data)))


def publish_results(db: Session, store: ObjectStore, engine, project: Project, run_id: str, project_dir: Path,
                    include_state: bool = True) -> None:
    """Upload derived artifacts. Graph versions are immutable, so existing ones are not rewritten."""
    if include_state:
        put_artifact(db, store, project, run_id, "state", state_key(project), engine.pack_project(project_dir))
    try:
        doc = engine.document(project_dir)
    except Exception:
        doc = ""
    if doc:
        put_artifact(db, store, project, run_id, "document", results_key(project, "document.md"), doc.encode())
    try:
        cl = engine.changelog(project_dir)
        if cl:
            put_artifact(db, store, project, run_id, "changelog", results_key(project, "changelog.md"), cl.encode())
    except Exception:
        pass
    try:
        rs = engine.review_state(project_dir)
        put_artifact(db, store, project, run_id, "review_state", results_key(project, "review_state.json"),
                     json.dumps(rs).encode())
    except Exception:
        pass
    try:
        versions = engine.graph_versions(project_dir)
    except Exception:
        versions = []
    put_artifact(db, store, project, run_id, "graph_versions", results_key(project, "graph/versions.json"),
                 json.dumps(versions).encode())
    for v in versions:
        n = v["version"]
        k = results_key(project, f"graph/v{int(n)}.json")
        if not store.exists(k):
            put_artifact(db, store, project, run_id, "graph", k, json.dumps(engine.graph_snapshot(project_dir, int(n))).encode())
    html = project_dir / "docs" / "graph.html"
    if html.is_file() and html.stat().st_size < 50 * 1024 * 1024:
        put_artifact(db, store, project, run_id, "graph_html", results_key(project, "graph.html"), html.read_bytes())
    db.commit()


__all__ = ["StoreError"]


def materialize_for_estimate(db: Session, store: ObjectStore, engine, settings: Settings, project: Project, cfg: dict,
                             incremental: bool, git_clone=None) -> dict:
    """Dry-run estimate in a throw-away scratch dir (ingest + extract + engine.estimate). No model calls."""
    src = latest_source(db, project.id)
    if src is None:
        raise ValueError("no source")
    with scratch_dir(settings, "ma-est-") as scratch:
        pdir = scratch / "project"
        use_state = bool(incremental and has_state(store, project))
        if use_state:
            unpack_state(store, engine, project, pdir)
        sdir = source_input(db, store, settings, project, src, scratch, git_clone)
        r = engine.run_stage(pdir, "ingest", source=str(sdir), incremental=use_state, config=cfg)
        if r.status != "ok":
            raise RuntimeError("ingest failed")
        r = engine.run_stage(pdir, "extract", config=cfg)
        if r.status != "ok":
            raise RuntimeError("extract failed")
        est = engine.estimate(pdir, cfg, incremental=use_state)
        return {"chunks": int(est.get("chunks", 0)), "usd": float(est.get("usd", 0.0))}
