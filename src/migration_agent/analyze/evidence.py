"""Deterministic evidence verifier (B-11): the 'zero unverified evidence' gate."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..graph.models import Evidence, Graph

KEEP_TYPES = {"dead_code", "config"}


class LogEntry(BaseModel):
    item_id: str
    action: str  # evidence_dropped | demoted | rejected | kept
    reason: str
    evidence: dict | None = None


class VerifyResult(BaseModel):
    graph: Graph
    demoted: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    log: list[LogEntry] = Field(default_factory=list)


class EvidenceError(Exception):
    pass


def _norm(s: str) -> str:
    return " ".join(s.split())


def check_evidence(ev: Evidence, code_dir: Path) -> str | None:
    """Return None if valid, else the failure reason."""
    root = Path(code_dir).resolve()
    try:
        path = (root / ev.file).resolve()
    except (OSError, ValueError, RuntimeError):
        return "unresolvable path"
    if Path(ev.file).is_absolute() or not path.is_relative_to(root):
        return "path escapes code_dir"
    if not path.is_file():
        return "file does not exist"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"unreadable file: {e}"
    if ev.line_start is not None:
        end = ev.line_end if ev.line_end is not None else ev.line_start
        if end > len(lines):
            return f"line range {ev.line_start}-{end} outside file ({len(lines)} lines)"
        region = lines[ev.line_start - 1:end]
    else:
        region = lines
    text = "\n".join(region)
    if ev.symbol and ev.symbol not in text:
        return f"symbol {ev.symbol!r} not found in cited lines"
    if ev.quote and _norm(ev.quote) not in _norm(text):
        return "quote not found in cited lines"
    return None


def verify_evidence(graph: Graph, code_dir: Path) -> VerifyResult:
    code_dir = Path(code_dir)
    log: list[LogEntry] = []
    demoted: list[str] = []
    rejected: list[str] = []

    def filt(item_id: str, evs: list[Evidence]) -> tuple[list[Evidence], list[Evidence]]:
        ok, bad = [], []
        for e in evs:
            r = check_evidence(e, code_dir)
            if r is None:
                ok.append(e)
            else:
                bad.append(e)
                log.append(LogEntry(item_id=item_id, action="evidence_dropped", reason=r,
                                    evidence=e.model_dump(mode="json")))
        return ok, bad

    new_nodes, dead = [], set()
    for n in sorted(graph.nodes, key=lambda x: x.id):
        ok, bad = filt(n.id, n.evidence)
        if ok:
            new_nodes.append(n.model_copy(update={"evidence": ok}))
        elif n.type in KEEP_TYPES and n.inventory_refs:
            # keep at low confidence: retain only file-level (verifiable) evidence if the file exists
            fallback = next((Evidence(file=e.file) for e in bad
                             if (check_evidence(Evidence(file=e.file), code_dir) is None)), None)
            new_nodes.append(n.model_copy(update={"evidence": [fallback] if fallback else []}))
            demoted.append(n.id)
            log.append(LogEntry(item_id=n.id, action="demoted",
                                reason="no valid evidence; kept via inventory_refs at low confidence"))
        else:
            dead.add(n.id)
            rejected.append(n.id)
            log.append(LogEntry(item_id=n.id, action="rejected", reason="no valid evidence"))

    new_edges = []
    for e in sorted(graph.edges, key=lambda x: x.id):
        if e.source in dead or e.target in dead:
            rejected.append(e.id)
            log.append(LogEntry(item_id=e.id, action="rejected", reason="endpoint node rejected"))
            continue
        ok, _ = filt(e.id, e.evidence)
        if ok:
            new_edges.append(e.model_copy(update={"evidence": ok}))
        else:
            rejected.append(e.id)
            log.append(LogEntry(item_id=e.id, action="rejected", reason="no valid evidence"))

    out = Graph(version=graph.version, nodes=new_nodes, edges=new_edges)
    return VerifyResult(graph=out, demoted=demoted, rejected=rejected, log=log)


def assert_all_evidence_valid(graph: Graph, code_dir: Path) -> None:
    problems = []
    for item in [*graph.nodes, *graph.edges]:
        for e in item.evidence:
            r = check_evidence(e, Path(code_dir))
            if r:
                problems.append(f"{item.id}: {e.file}: {r}")
    if problems:
        raise EvidenceError("unverified evidence remains:\n" + "\n".join(problems))
