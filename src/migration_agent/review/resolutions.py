"""C-03 resolution engine: feedback/resolutions.json (one resolution per finding)."""
from __future__ import annotations

from ..project import Project
from ..verify.models import FindingsFile, Resolution, ResolutionsFile, ResolutionStatus
from ._io import ReviewError, atomic_write, now_iso

PENDING_STATUSES = ("needs_reanalysis", "accepted_doc")


def _path(project: Project):
    return project.root / "feedback" / "resolutions.json"


def load_findings(project: Project) -> FindingsFile:
    p = project.root / "feedback" / "findings.json"
    if not p.exists():
        return FindingsFile()
    return FindingsFile.model_validate_json(p.read_text())


def load_resolutions(project: Project) -> ResolutionsFile:
    p = _path(project)
    if not p.exists():
        return ResolutionsFile()
    return ResolutionsFile.model_validate_json(p.read_text())


def _save(project: Project, rf: ResolutionsFile) -> None:
    atomic_write(_path(project), rf.model_dump_json(indent=2) + "\n")


def add_resolution(project: Project, finding_id: str, status: ResolutionStatus, note: str = "") -> Resolution:
    finding = next((f for f in load_findings(project).findings if f.id == finding_id), None)
    if finding is None:
        raise ReviewError(f"unknown finding: {finding_id}")
    res = Resolution(finding_id=finding_id, status=status, note=note,
                     affected_node_ids=list(dict.fromkeys(finding.item_ids)), created=now_iso())
    rf = load_resolutions(project)
    kept = [r for r in rf.resolutions if r.finding_id != finding_id]
    rf.resolutions = kept + [res]
    _save(project, rf)
    return res


def pending_reanalysis(project: Project) -> list[Resolution]:
    return [r for r in load_resolutions(project).resolutions if r.status in PENDING_STATUSES]


def mark_resolved(project: Project, finding_ids: list[str]) -> None:
    rf = load_resolutions(project)
    ids = set(finding_ids)
    rf.resolutions = [r.model_copy(update={"status": "resolved"}) if r.finding_id in ids else r
                      for r in rf.resolutions]
    _save(project, rf)
