"""C-05 comment system: feedback/comments.json."""
from __future__ import annotations

from ..graph import load_latest
from ..ingest.secrets import redact_string
from ..project import Project
from ..verify.models import Comment, CommentsFile
from ._io import ReviewError, atomic_write, now_iso


def _path(project: Project):
    return project.root / "feedback" / "comments.json"


def load_comments(project: Project) -> CommentsFile:
    p = _path(project)
    if not p.exists():
        return CommentsFile()
    return CommentsFile.model_validate_json(p.read_text())


def _save(project: Project, cf: CommentsFile) -> None:
    atomic_write(_path(project), cf.model_dump_json(indent=2) + "\n")


def add_comment(project: Project, item_id: str, text: str, author: str = "user") -> Comment:
    if not text.strip():
        raise ReviewError("comment text is empty")
    graph = load_latest(project.graph_dir)
    known = {n.id for n in graph.nodes} | {e.id for e in graph.edges} if graph else set()
    if item_id not in known:
        raise ReviewError(f"unknown graph item: {item_id}")
    cf = load_comments(project)
    nums = [int(c.id.split("-")[1]) for c in cf.comments if c.id.startswith("CM-") and c.id[3:].isdigit()]
    # comments flow into re-analysis prompts: never persist pasted credentials
    c = Comment(id=f"CM-{max(nums, default=0) + 1:04d}", item_id=item_id, text=redact_string(text), author=redact_string(author),
                created=now_iso())
    cf.comments.append(c)
    _save(project, cf)
    return c


def unprocessed(project: Project) -> list[Comment]:
    return [c for c in load_comments(project).comments if not c.processed]


def mark_processed(project: Project, comment_ids: list[str]) -> None:
    cf = load_comments(project)
    ids = set(comment_ids)
    cf.comments = [c.model_copy(update={"processed": True}) if c.id in ids else c for c in cf.comments]
    _save(project, cf)
