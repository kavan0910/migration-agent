"""Worker: ``python -m app.worker`` (contract section 5).

Claims jobs from the DB queue, materialises the project from the ObjectStore into a scratch dir, runs engine stages,
streams events into run_events/usage_events, uploads artifacts, enforces budget and entitlements, retries with
backoff, is idempotent per run (completed stages are recorded and skipped on retry) and always removes the scratch
directory. The model API key comes from server config and is passed per call; it is never written anywhere.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit, engine as engine_mod, jobs, materialize as mat
from .billing import get_billing, remaining_monthly_usd
from .config import Settings, get_settings
from .db import Database, set_rls_context
from .logging_setup import configure_logging, run_id_var
from .models import Comment, Job, Project, Resolution, Run, UsageEvent
from .profiles import engine_config
from .safety import UnsafeInput
from .services import deletion
from .services.runs import TERMINAL, add_event
from .storage import ObjectStore, make_store

log = logging.getLogger("app.worker")

STAGES = {"analyze": ("ingest", "extract", "analyze"), "verify": ("verify",), "reanalyze": ("resolve",)}
_TRANSIENT = ("timeout", "timed out", "rate limit", "ratelimit", "429", "503", "502", "overloaded", "unavailable",
              "connection", "temporarily")


class RunAborted(Exception):
    """Cancelled by the user or the project was deleted."""


class RetryableError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scrub(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:500]


class EventSink:
    """Thread-safe bridge from engine callbacks (any thread) to the database."""

    def __init__(self, db: Database, run_id: str, workspace_id: str, project_id: str, job_id: str, max_usd: float,
                 secret: str | None):
        self.db, self.run_id, self.wid, self.pid, self.job_id = db, run_id, workspace_id, project_id, job_id
        self.max_usd, self.secret = max_usd, secret
        self.lock = threading.Lock()
        self.cost = 0.0
        self.stage_cost: dict[str, float] = {}
        self.stop_reason: str | None = None
        self._last_progress = 0.0
        self._last_beat = time.monotonic()
        self._last_check = 0.0

    def __call__(self, ev: dict) -> None:
        try:
            self._handle(ev)
        except Exception:  # never break the engine because of bookkeeping
            log.warning("event sink failure", extra={"event": "sink_error"})

    def _handle(self, ev: dict) -> None:
        typ = ev.get("type")
        with self.lock:
            with self.db.session() as s:
                if typ == "progress":
                    now = time.monotonic()
                    done, total = int(ev.get("done", 0)), int(ev.get("total", 0))
                    if now - self._last_progress < 0.25 and done < total:
                        return
                    self._last_progress = now
                    add_event(s, self.run_id, self.wid, "progress", {"stage": ev.get("stage"), "done": done, "total": total})
                elif typ == "usage":
                    usd = float(ev.get("usd", 0.0) or 0.0)
                    s.add(UsageEvent(workspace_id=self.wid, project_id=self.pid, run_id=self.run_id,
                                     stage=str(ev.get("stage", ""))[:20], task=str(ev.get("task", ""))[:60],
                                     model=str(ev.get("model", ""))[:120], input_tokens=int(ev.get("input_tokens", 0) or 0),
                                     output_tokens=int(ev.get("output_tokens", 0) or 0), usd=usd))
                    run = s.get(Run, self.run_id)
                    if run is not None:
                        run.cost_usd = (run.cost_usd or 0.0) + usd
                        self.cost = run.cost_usd
                    self.stage_cost[str(ev.get("stage"))] = self.stage_cost.get(str(ev.get("stage")), 0.0) + usd
                    add_event(s, self.run_id, self.wid, "usage", {
                        "stage": ev.get("stage"), "task": ev.get("task"), "model": ev.get("model"),
                        "input_tokens": ev.get("input_tokens"), "output_tokens": ev.get("output_tokens"), "usd": usd,
                        "cost_usd": round(self.cost, 6)})
                elif typ == "log":
                    add_event(s, self.run_id, self.wid, "log", {
                        "level": str(ev.get("level", "info"))[:10], "message": _scrub(str(ev.get("message", "")), self.secret)})
            self._housekeeping()

    def _housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_beat > 10:
            self._last_beat = now
            with self.db.session() as s:
                jobs.heartbeat(s, self.job_id)
        if now - self._last_check > 2:
            self._last_check = now
            with self.db.session() as s:
                run = s.get(Run, self.run_id)
                if run is None or s.get(Project, self.pid) is None:
                    self.stop_reason = "deleted"
                elif run.cancel_requested:
                    self.stop_reason = "cancelled"


class Worker:
    def __init__(self, settings: Settings | None = None, db: Database | None = None, store: ObjectStore | None = None,
                 worker_id: str | None = None, git_clone=None):
        self.st = settings or get_settings()
        self.db = db or Database(self.st.database_url)
        self.db.bypass_rls = True  # the worker is a trusted service principal (RLS policy 'app.bypass')
        self.store = store or make_store(self.st)
        self.id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.git_clone = git_clone
        self.stop = threading.Event()
        self._last_reap = 0.0 if self.st.reaper_interval_s <= 0 else time.monotonic()
        self._last_reconcile = time.monotonic()

    # ---------------------------------------------------------------------------------- loop
    def run_forever(self) -> None:
        log.info("worker started", extra={"event": "start"})
        while not self.stop.is_set():
            try:
                busy = self.run_once()
            except Exception as e:  # DB down / schema missing: keep the process alive, retry
                log.error("worker loop error: %s", type(e).__name__, extra={"event": "loop_error"})
                busy = False
                self.stop.wait(max(self.st.worker_poll_s, 5.0))
            if not busy:
                self.stop.wait(self.st.worker_poll_s)

    def reap_deletions(self, reconcile: bool = False) -> dict:
        try:
            return deletion.reap(self.db, self.store, self.st, reconcile=reconcile)
        except Exception as e:  # never let housekeeping stop job processing
            log.error("deletion reaper error: %s", type(e).__name__, extra={"event": "reaper_error"})
            return {}

    def run_once(self) -> bool:
        now = time.monotonic()
        if now - self._last_reap >= self.st.reaper_interval_s:
            self.reap_deletions(reconcile=now - self._last_reconcile >= self.st.reconcile_interval_s)
            if now - self._last_reconcile >= self.st.reconcile_interval_s:
                self._last_reconcile = now
            self._last_reap = now
        with self.db.session() as s:
            set_rls_context(s, None, bypass=True)
            jobs.reap_stale(s, self.st.job_lock_timeout_s)
            job = jobs.claim(s, self.id)
        if job is None:
            return False
        self.process(job.id)
        return True

    # ---------------------------------------------------------------------------------- job
    def process(self, job_id: str) -> None:
        with self.db.session() as s:
            set_rls_context(s, None, bypass=True)
            job = s.get(Job, job_id)
            run = s.get(Run, job.run_id) if job else None
            project = s.get(Project, run.project_id) if run else None
            if job is None or run is None or project is None:
                if job:
                    job.status, job.last_error = "cancelled", "run or project deleted"
                    s.commit()
                return
            if run.status in TERMINAL:
                job.status = "done"
                s.commit()
                return
            if not jobs.try_lock_project(s, project.id, run.id):  # another run of this project is executing
                job.status, job.locked_by, job.attempts = "queued", None, max(0, job.attempts - 1)
                job.available_at = _now().replace(microsecond=0) + timedelta(seconds=5)
                s.commit()
                return
            attempts, max_attempts = job.attempts, job.max_attempts
            rid, pid, wid = run.id, project.id, project.workspace_id
        token = run_id_var.set(rid)
        beat_stop = threading.Event()

        def _beat() -> None:
            while not beat_stop.wait(max(1.0, self.st.job_lock_timeout_s / 4)):
                try:
                    with self.db.session() as hs:
                        jobs.heartbeat(hs, job_id)
                except Exception:
                    pass

        threading.Thread(target=_beat, daemon=True).start()
        try:
            outcome, err = self._execute(job_id, rid, attempts)
        except RunAborted as e:
            outcome, err = ("cancelled" if str(e) == "cancelled" else "deleted"), None
        except RetryableError as e:
            outcome, err = "retry", str(e)
        except UnsafeInput as e:
            outcome, err = "failed", str(e)
        except Exception as e:  # noqa: BLE001 - infrastructure failure: retry with backoff
            log.error("job crashed: %s", type(e).__name__, extra={"event": "job_crash"})
            outcome, err = "retry", f"{type(e).__name__}"
        finally:
            beat_stop.set()
            run_id_var.reset(token)
        self._finish(job_id, rid, pid, wid, outcome, err, attempts, max_attempts)

    def _finish(self, job_id: str, rid: str, pid: str, wid: str, outcome: str, err: str | None, attempts: int,
                max_attempts: int) -> None:
        with self.db.session() as s:
            set_rls_context(s, None, bypass=True)
            job, run = s.get(Job, job_id), s.get(Run, rid)
            try:
                if outcome == "retry" and attempts < max_attempts and run is not None and not run.cancel_requested:
                    delay = jobs.backoff_delay(attempts, self.st.job_backoff_base_s)
                    job.status, job.locked_by, job.last_error = "queued", None, (err or "")[:500]
                    job.available_at = _now() + timedelta(seconds=delay)
                    run.status = "queued"
                    add_event(s, rid, wid, "status", {"status": "retrying", "attempt": attempts, "in_s": round(delay, 1)})
                    return
                if outcome == "retry":
                    outcome, err = "failed", err or "retries exhausted"
                if run is None:  # project deleted mid-run: nothing left to update
                    if job:
                        job.status = "cancelled"
                    return
                status = {"succeeded": "succeeded", "stopped_budget": "stopped_budget", "cancelled": "cancelled",
                          "failed": "failed"}.get(outcome)
                if outcome == "deleted" or status is None:
                    if job:
                        job.status = "cancelled"
                    return
                run.status, run.finished_at, run.error = status, _now(), (err[:1000] if err else None)
                run.current_stage = None
                job.status = "done" if status != "failed" else "failed"
                job.last_error = (err or "")[:500] or None
                add_event(s, rid, wid, "status", {"status": status, "cost_usd": round(run.cost_usd or 0.0, 6),
                                                  **({"error": run.error} if run.error else {})})
                audit.record(s, action="run.finish", workspace_id=wid, actor=None, target=rid,
                             meta={"status": status, "cost_usd": round(run.cost_usd or 0.0, 6)})
                try:
                    get_billing().report_usage(wid, {"run_id": rid, "usd": run.cost_usd})
                except Exception:
                    log.warning("billing seam failed", extra={"event": "billing_error"})
            finally:
                s.commit()
                jobs.unlock_project(s, pid, rid)

    # ---------------------------------------------------------------------------------- execution
    def _execute(self, job_id: str, rid: str, attempt: int) -> tuple[str, str | None]:
        eng = engine_mod.get_engine()
        st = self.st
        with self.db.session() as s:
            set_rls_context(s, None, bypass=True)
            run = s.get(Run, rid)
            project = s.get(Project, run.project_id)
            run.status = "running"
            run.started_at = run.started_at or _now()
            run.engine_version = getattr(eng, "version", None)
            add_event(s, rid, run.workspace_id, "status", {"status": "running", "attempt": attempt})
            snapshot = dict(id=run.id, wid=run.workspace_id, pid=project.id, type=run.type, profile=run.model_profile,
                            max_usd=run.max_usd, incremental=run.incremental, done=list(run.completed_stages or []))
        cfg = engine_config(snapshot["profile"], st)
        secret = cfg.get("api_key")
        sink = EventSink(self.db, rid, snapshot["wid"], snapshot["pid"], job_id, snapshot["max_usd"], secret)
        stages = [x for x in STAGES[snapshot["type"]]]
        with mat.scratch_dir(st) as scratch:
            pdir = scratch / "project"
            with self.db.session() as s:
                set_rls_context(s, None, bypass=True)
                project = s.get(Project, snapshot["pid"])
                had_state = mat.has_state(self.store, project)
                if had_state:
                    mat.unpack_state(self.store, eng, project, pdir)
                src = mat.latest_source(s, project.id)
                ingested_id = project.ingested_source_id
                self._apply_review(s, eng, project, pdir)
            source_dir: Path | None = None
            final = "succeeded"
            err: str | None = None
            for stage in stages:
                self._check(sink)
                if stage in snapshot["done"]:
                    continue
                if stage == "ingest" and had_state and src is not None and ingested_id == src.id:
                    self._mark_stage(rid, stage)  # already ingested into the stored state
                    continue
                remaining = self._remaining_budget(snapshot, sink)
                if stage in ("analyze", "verify", "resolve") and remaining <= 1e-9:
                    return "stopped_budget", "budget exhausted"
                with self.db.session() as s:
                    r = s.get(Run, rid)
                    r.current_stage = stage
                    add_event(s, rid, snapshot["wid"], "stage", {"stage": stage, "status": "started"})
                kw: dict = dict(config=cfg, max_usd=remaining, workers=st.engine_workers, offline=st.engine_offline,
                                on_event=sink)
                if stage == "ingest":
                    with self.db.session() as s:
                        project = s.get(Project, snapshot["pid"])
                        source = mat.latest_source(s, project.id)
                        source_dir = mat.source_input(s, self.store, st, project, source, scratch, self.git_clone)
                    kw.update(source=str(source_dir), incremental=had_state)
                elif stage == "analyze":
                    kw.update(incremental=snapshot["incremental"])
                elif stage == "resolve":
                    kw.update(reanalyze=True)
                before = sink.cost
                result = eng.run_stage(pdir, stage, **kw)
                self._reconcile(sink, snapshot, stage, result, before)
                self._check(sink)
                if result.status == "ok":
                    self._mark_stage(rid, stage, ingested=src.id if stage == "ingest" and src else None,
                                     project_dir=pdir, snapshot=snapshot)
                    continue
                err = getattr(result, "error", None)
                if result.status == "stopped_budget":
                    final = "stopped_budget"
                    break
                if any(m in (err or "").lower() for m in _TRANSIENT):
                    raise RetryableError(_scrub(err or "transient engine error", secret))
                return "failed", _scrub(err or f"{stage} failed", secret)
            # publish (also after a budget stop: partial results are useful)
            self._publish(snapshot, pdir)
            return final, (_scrub(err, secret) if err and final != "succeeded" else None)

    def _check(self, sink: EventSink) -> None:
        with self.db.session() as s:
            set_rls_context(s, None, bypass=True)
            run = s.get(Run, sink.run_id)
            if run is None or s.get(Project, sink.pid) is None:
                raise RunAborted("deleted")
            if run.cancel_requested:
                raise RunAborted("cancelled")
        if sink.stop_reason:
            raise RunAborted(sink.stop_reason)

    def _remaining_budget(self, snap: dict, sink: EventSink) -> float:
        with self.db.session() as s:
            set_rls_context(s, None, bypass=True)
            spent = s.get(Run, snap["id"]).cost_usd or 0.0
            monthly = remaining_monthly_usd(s, snap["wid"], exclude_run_id=snap["id"])  # other runs' reservations are off-limits
        rem = snap["max_usd"] - spent
        if monthly is not None:
            rem = min(rem, monthly)
        return rem

    def _reconcile(self, sink: EventSink, snap: dict, stage: str, result, before: float) -> None:
        """If the engine reports more cost than it streamed as usage events, meter the difference."""
        delta = float(getattr(result, "cost_usd", 0.0) or 0.0) - (sink.cost - before)
        if delta > 1e-6:
            with self.db.session() as s:
                s.add(UsageEvent(workspace_id=snap["wid"], project_id=snap["pid"], run_id=snap["id"], stage=stage,
                                 task="unattributed", model="", usd=delta))
                run = s.get(Run, snap["id"])
                run.cost_usd = (run.cost_usd or 0.0) + delta
                sink.cost = run.cost_usd

    def _mark_stage(self, rid: str, stage: str, ingested: str | None = None, project_dir: Path | None = None,
                    snapshot: dict | None = None) -> None:
        if project_dir is not None and snapshot is not None:  # persist state so a retry can resume elsewhere
            self._publish(snapshot, project_dir, results=False)
        with self.db.session() as s:
            run = s.get(Run, rid)
            run.completed_stages = list(run.completed_stages or []) + [stage]
            if ingested:
                s.get(Project, run.project_id).ingested_source_id = ingested
            add_event(s, rid, run.workspace_id, "stage", {"stage": stage, "status": "done"})

    def _publish(self, snap: dict, pdir: Path, results: bool = True) -> None:
        eng = engine_mod.get_engine()
        with self.db.session() as s:
            set_rls_context(s, None, bypass=True)
            project = s.get(Project, snap["pid"])
            if project is None:
                raise RunAborted("deleted")
            if results:
                mat.publish_results(s, self.store, eng, project, snap["id"], pdir)
            else:
                mat.put_artifact(s, self.store, project, snap["id"], "state", mat.state_key(project), eng.pack_project(pdir))
            s.commit()
            if s.get(Project, snap["pid"]) is None:  # deleted while uploading: do not resurrect data
                t = deletion.enqueue(s, prefix=f"{snap['wid']}/{snap['pid']}/", workspace_id=snap["wid"],
                                     project_id=snap["pid"], reason="deleted_during_run")
                s.commit()
                deletion.attempt(s, self.store, t, self.st)
                s.commit()
                raise RunAborted("deleted")

    def _apply_review(self, s: Session, eng, project: Project, pdir: Path) -> None:
        """Replay API-recorded comments/resolutions into the engine's project folder before running stages."""
        if not pdir.exists():
            return
        for c in s.scalars(select(Comment).where(Comment.project_id == project.id, Comment.applied.is_(False))
                           .order_by(Comment.created_at)):
            try:
                eng.add_comment(pdir, c.item_id, c.text, c.author_name or None)
            except Exception:
                pass  # item no longer exists in this state; do not retry forever
            c.applied = True
        for r in s.scalars(select(Resolution).where(Resolution.project_id == project.id, Resolution.applied.is_(False))
                           .order_by(Resolution.created_at)):
            try:
                eng.add_resolution(pdir, r.finding_id, r.status, r.note, r.author_name or None)
            except Exception:
                pass
            r.applied = True
        s.commit()


def main() -> None:  # pragma: no cover
    st = get_settings()
    configure_logging(st.log_level, st.log_json)
    w = Worker(st)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: w.stop.set())
    w.run_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
