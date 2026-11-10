"""Library facade over the engine for a hosted backend (platform contract section 2).

Pure functions over a local *project directory* (the same folder layout the CLI uses). No HTTP, no database, no
network other than the configured model endpoint. Importing this module does not import the `llm` extra; it is only
needed when a real (non-offline, non-injected) model is used.

Credentials and budget are per call: `config` may carry `api_key`, model names and pricing, and when it does the
process environment is never consulted for them. `max_usd` is enforced by the engine (pre-call by the client's
budget, post-call by a metering wrapper), and a run that hits it stops gracefully with `status="stopped_budget"`.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ValidationError

from .ingest.secrets import redact_string
from .storage import ArchiveError, pack_project, unpack_project  # noqa: F401  (re-exported public API)

log = logging.getLogger(__name__)

Event = dict
STAGES = ("ingest", "extract", "analyze", "verify", "resolve")


class ServiceError(ValueError):
    """Invalid request (unknown item, bad status, empty text ...). `code` is "not_found" or "invalid"."""

    def __init__(self, message: str, code: str = "invalid"):
        super().__init__(message)
        self.code = code


class StageResult(BaseModel):
    stage: str
    status: Literal["ok", "stopped_budget", "failed"]
    summary: dict = {}
    cost_usd: float = 0.0
    error: str | None = None


# --------------------------------------------------------------------------- events / metering
class _Emitter:
    """Thread-safe, never raises into the engine: a broken callback must not kill a run."""

    def __init__(self, cb: Callable[[Event], None] | None, secrets: tuple[str, ...] = ()):
        self.cb = cb
        self.secrets = tuple(x for x in secrets if x)
        self._lock = threading.Lock()

    def scrub(self, text: str) -> str:
        for sec in self.secrets:           # the run's explicit credential must never leave via errors or logs
            text = text.replace(sec, "[REDACTED:api_key]")
        return redact_string(text)

    def __call__(self, ev: Event) -> None:
        if self.cb is None:
            return
        with self._lock:
            try:
                self.cb(ev)
            except Exception:  # noqa: BLE001
                log.exception("on_event callback raised; ignored")

    def log(self, level: str, message: str) -> None:
        self({"type": "log", "level": level, "message": self.scrub(str(message))})


class _EventLLM:
    """Wraps any LLMClient: emits one `usage` event per call and tallies spend (thread-safe).
    Records the budget/auth exception that stopped a run so the stage can report the right status."""

    def __init__(self, inner, stage: str, emit: _Emitter):
        self.inner, self.stage, self.emit = inner, stage, emit
        self.usd = 0.0
        self.stop_exc: BaseException | None = None
        self._lock = threading.Lock()

    def complete(self, **kw):
        from .llm.base import BudgetExceeded, LLMAuthError
        try:
            r = self.inner.complete(**kw)
        except (BudgetExceeded, LLMAuthError) as e:
            with self._lock:
                self.stop_exc = self.stop_exc or e
            raise
        usd = float(getattr(r, "cost_usd", 0.0) or 0.0)
        with self._lock:
            self.usd += usd
        self.emit({"type": "usage", "stage": self.stage, "task": kw.get("task", ""),
                   "model": getattr(r, "model", "") or "", "input_tokens": int(getattr(r, "tokens_in", 0) or 0),
                   "output_tokens": int(getattr(r, "tokens_out", 0) or 0), "usd": usd})
        return r

    def __getattr__(self, name):  # config, calls ... of the wrapped client
        return getattr(self.inner, name)


def _llm_config(config: dict | None):
    """LLMConfig from the explicit dict only (never from MA_* environment variables)."""
    from .llm.litellm_client import LLMConfig
    data = dict(config or {})
    if isinstance(data.get("llm"), dict):
        data = {**data, **data.pop("llm")}
    fields = set(LLMConfig.model_fields)
    return LLMConfig.model_validate({k: v for k, v in data.items() if k in fields})


def _build_llm(proj, config, max_usd, offline, llm):
    if llm is not None:
        return llm
    if offline:
        from .llm.offline import OfflineLLM
        return OfflineLLM()
    from .audit import AuditLog
    from .llm.litellm_client import Budget, LiteLLMClient
    return LiteLLMClient(_llm_config(config), budget=Budget(max_usd=max_usd), cache_dir=proj.root / "llm_cache",
                         audit=AuditLog(proj.audit_dir / "audit.jsonl"))


# --------------------------------------------------------------------------- stages
def run_stage(project_dir: Path, stage: str, *, source: str | None = None, config: dict | None = None,
              max_usd: float | None = None, workers: int = 1, incremental: bool = False, offline: bool = False,
              on_event: Callable[[Event], None] | None = None, llm: object | None = None,
              finding_id: str | None = None, status: str | None = None, note: str | None = None,
              reanalyze: bool = False) -> StageResult:
    """Run one pipeline stage on `project_dir`.

    ingest   copy `source` (path/zip/tar/git URL; defaults to project.json's source) into code/. Creates the
             project folder when `source` is given and project.json is missing. `incremental` = refresh code/.
    extract  static facts + coverage inventory (no LLM).
    analyze  extract if needed -> LLM analysis -> merge -> verify evidence -> doc + graph. Honours workers,
             incremental, max_usd, offline, llm.
    verify   blind Verifier over user_docs/ -> feedback/findings.json (needs the understanding doc).
    resolve  optionally record `finding_id`+`status`(+`note`); with reanalyze=True run targeted re-analysis of
             pending resolutions/comments and regenerate docs/changelog.md.
    Expected failures (missing project, bad input, unknown finding, provider errors) come back as status="failed", never raised;
    an unknown `stage` raises ServiceError.
    """
    if stage not in STAGES:
        raise ServiceError(f"unknown stage {stage!r}; expected one of {', '.join(STAGES)}")
    emit = _Emitter(on_event, ((config or {}).get("api_key") or "",))
    if max_usd is None and config and config.get("max_usd") is not None:
        max_usd = float(config["max_usd"])
    ev_llm: _EventLLM | None = None
    try:
        proj = _open_project(Path(project_dir), stage, source)
        emit.log("info", f"stage {stage}: start")
        if stage == "ingest":
            summary = _ingest(proj, source, incremental)
            emit({"type": "progress", "stage": stage, "done": 1, "total": 1})
            return _done(stage, "ok", summary, 0.0, emit)
        if stage == "extract":
            from .pipeline import run_extract_stage
            inv = run_extract_stage(proj)
            emit({"type": "progress", "stage": stage, "done": 1, "total": 1})
            return _done(stage, "ok", {"items": len(inv.items), "counts": inv.counts, "warnings": inv.warnings}, 0.0, emit)

        if stage == "verify" and not (proj.docs / "understanding.latest.md").exists():
            return _done(stage, "failed", {}, 0.0, emit, "No understanding doc yet. Run analyze first.")
        if stage == "resolve":
            summary, ev_llm = _resolve(proj, emit, config, max_usd, offline, llm, finding_id, status, note, reanalyze)
        else:
            inner = _build_llm(proj, config, max_usd, offline, llm)
            ev_llm = _EventLLM(inner, stage, emit)
            summary = _analyze(proj, ev_llm, emit, config, max_usd, workers, incremental, offline, llm) \
                if stage == "analyze" else _verify(proj, ev_llm, max_usd)
        stopped = summary.pop("_stopped", None)
        cost = ev_llm.usd if ev_llm else 0.0
        if stopped:
            kind = "failed" if _is_auth(ev_llm) else "stopped_budget"
            return _done(stage, kind, summary, cost, emit, stopped)
        return _done(stage, "ok", summary, cost, emit)
    except Exception as e:  # noqa: BLE001
        from .llm.base import BudgetExceeded
        cost = ev_llm.usd if ev_llm else 0.0
        st = "stopped_budget" if isinstance(e, BudgetExceeded) and not _is_auth(ev_llm) else "failed"
        return _done(stage, st, {}, cost, emit, f"{type(e).__name__}: {e}")


def _is_auth(ev_llm) -> bool:
    from .llm.base import LLMAuthError
    return ev_llm is not None and isinstance(ev_llm.stop_exc, LLMAuthError)


def _done(stage, status, summary, cost, emit: _Emitter, error: str | None = None) -> StageResult:
    err = emit.scrub(error) if error else None
    emit.log("error" if status == "failed" else "warning" if status != "ok" else "info",
             f"stage {stage}: {status}" + (f" ({err})" if err else ""))
    return StageResult(stage=stage, status=status, summary=summary, cost_usd=round(cost, 6), error=err)


def _open_project(path: Path, stage: str, source: str | None):
    from .project import Project
    if stage == "ingest" and source is not None and not (path / "project.json").exists():
        path.mkdir(parents=True, exist_ok=True)
        return Project.init(path, source)
    return Project.load(path)


def _ingest(proj, source, refresh) -> dict:
    from .pipeline import run_ingest_stage
    r = run_ingest_stage(proj, source, refresh=refresh)
    return {"files_copied": r.files_copied, "files_skipped": r.files_skipped,
            "secret_findings": len(r.secret_findings), "source_type": r.source_type,
            "source_hash": r.source_hash}


def _analyze(proj, ev_llm: _EventLLM, emit: _Emitter, config, max_usd, workers, incremental, offline, injected) -> dict:
    from .analyze.runner import estimate, run_analyze
    from .pipeline import ensure_extracted
    ensure_extracted(proj, force=incremental)
    if injected is None and not offline and max_usd is not None:
        n, usd = estimate(proj, _llm_config(config), incremental=incremental)
        emit.log("info", f"plan: {n} chunk(s), estimated cost ${usd:.2f}")
        if usd > max_usd:
            emit.log("warning", f"estimate ${usd:.2f} exceeds max_usd ${max_usd:.2f}; the run will stop gracefully")
    out = run_analyze(proj, ev_llm, log=lambda s: emit.log("info", s), incremental=incremental, workers=workers,
                      max_usd=max_usd,
                      on_progress=lambda d, t: emit({"type": "progress", "stage": "analyze", "done": d, "total": t}))
    summary = dict(out)
    if summary.get("stopped"):
        summary["_stopped"] = summary["stopped"]
    return summary


def _verify(proj, ev_llm: _EventLLM, max_usd) -> dict:
    from .analyze.runner import _Metered
    from .llm.base import BudgetExceeded, LLMAuthError
    from .verify import run_verifier
    llm = _Metered(ev_llm, max_usd)
    try:
        out = run_verifier(proj, llm)
    except (BudgetExceeded, LLMAuthError) as e:
        return {"_stopped": str(e)}
    by_kind: dict[str, int] = {}
    for f in out.findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    return {"findings": len(out.findings), "by_kind": by_kind, "dropped_unverifiable": out.dropped_unverifiable,
            "claims_extracted": out.claims_extracted}


def _resolve(proj, emit, config, max_usd, offline, injected, finding_id, status, note, do_reanalyze):
    from .analyze.runner import _Metered
    from .llm.base import BudgetExceeded, LLMAuthError
    from .review._io import ReviewError
    from .review.changelog import generate_changelog
    from .review.comments import unprocessed
    from .review.reanalyze import reanalyze
    from .review.resolutions import add_resolution as _record, pending_reanalysis
    summary: dict = {}
    try:
        if finding_id or status:
            if not (finding_id and status):
                raise ServiceError("give both finding_id and status")
            r = _record(proj, finding_id, status, note or "")
            summary["resolution"] = r.model_dump()
    except ReviewError as e:
        raise ServiceError(str(e), "not_found") from e
    except ValidationError as e:
        raise ServiceError(f"invalid status: {status!r}") from e
    if not do_reanalyze:
        return summary, None
    if not (pending_reanalysis(proj) or unprocessed(proj)):
        summary["nothing_to_reanalyze"] = True
        return summary, None
    ev = _EventLLM(_Metered(_build_llm(proj, config, max_usd, offline, injected), max_usd), "resolve", emit)
    try:
        res = reanalyze(proj, ev)
    except (BudgetExceeded, LLMAuthError) as e:
        summary["_stopped"] = str(e)
        return summary, ev
    if res.stopped:
        summary["_stopped"] = res.stopped
        return summary, ev
    summary.update(nodes_reanalyzed=res.nodes_reanalyzed, old_version=res.old_version, new_version=res.new_version,
                   added_nodes=len(res.added_nodes), changed_nodes=len(res.changed_nodes),
                   removed_nodes=len(res.removed_nodes))
    generate_changelog(proj)
    return summary, ev


def estimate(project_dir: Path, config: dict | None = None, incremental: bool = False) -> dict:
    """{"chunks": int, "usd": float} for the chunks still to analyse. Builds the (LLM-free) facts if missing."""
    from .analyze.runner import estimate as _estimate
    from .pipeline import ensure_extracted
    from .project import Project
    proj = Project.load(Path(project_dir))
    ensure_extracted(proj)
    n, usd = _estimate(proj, _llm_config(config), incremental=incremental)
    return {"chunks": n, "usd": round(usd, 6)}


# --------------------------------------------------------------------------- review (reuses review_ui.server.ReviewState)
_states: dict[Path, object] = {}
_states_lock = threading.Lock()


def _review(project_dir: Path):
    """One ReviewState (with its write lock) per project directory in this process."""
    from .project import Project
    from .review_ui.server import ReviewState
    proj = Project.load(Path(project_dir))
    with _states_lock:
        st = _states.get(proj.root)
        if st is None:
            st = _states[proj.root] = ReviewState(proj)
    return st


def _review_call(fn, *a):
    from .review_ui.server import ApiError
    try:
        return fn(*a)
    except ApiError as e:
        raise ServiceError(e.msg, "not_found" if e.code == 404 else "invalid") from e
    except ValidationError as e:
        raise ServiceError("invalid input: " + "; ".join(
            f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:300]) from e


def review_state(project_dir: Path) -> dict:
    """Same shape the review UI serves at /api/state."""
    return _review(project_dir).state()


def graph_versions(project_dir: Path) -> list[dict]:
    from .graph.store import load_snapshot
    st = _review(project_dir)
    return [{"version": v, "nodes": len((g := load_snapshot(st.project.graph_dir, v)).nodes), "edges": len(g.edges)}
            for v in st.versions()]


def graph_snapshot(project_dir: Path, version: int | None = None) -> dict:
    """{"version","nodes":[...],"edges":[...]} for `version` (default latest; empty graph at version 0 if none)."""
    from .graph.store import GraphStoreError, load_snapshot
    st = _review(project_dir)
    if version is None:
        g = st.graph()
        if g is None:
            return {"version": 0, "nodes": [], "edges": []}
    else:
        if version not in st.versions():
            raise ServiceError(f"unknown version: {version}", "not_found")
        try:
            g = load_snapshot(st.project.graph_dir, version)
        except GraphStoreError as e:
            raise ServiceError(str(e), "not_found") from e
    return g.model_dump(mode="json")


def graph_diff(project_dir: Path, a: int, b: int) -> dict:
    return _review_call(_review(project_dir).diff, a, b)


def add_comment(project_dir: Path, item_id: str, text: str, author: str | None = None) -> dict:
    return _review_call(_review(project_dir).add_comment,
                        {"item_id": item_id, "text": text, "author": author or "user"})


def add_resolution(project_dir: Path, finding_id: str, status: str, note: str = "", author: str | None = None) -> dict:
    """`author` is accepted for API symmetry; the resolution record has no author field."""
    return _review_call(_review(project_dir).add_resolution,
                        {"finding_id": finding_id, "status": status, "note": note})


def document(project_dir: Path) -> str:
    return _review(project_dir).doc_text()


def changelog(project_dir: Path) -> str:
    """Regenerate docs/changelog.md from graph version diffs and return it."""
    from .project import Project
    from .review.changelog import generate_changelog
    return generate_changelog(Project.load(Path(project_dir))).read_text(encoding="utf-8")
