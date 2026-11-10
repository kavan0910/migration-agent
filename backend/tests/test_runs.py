import logging
import os
import threading
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import jobs
from app.models import Artifact, Job, Project, Run, RunEvent, UsageEvent
from app.worker import Worker
from tests.conftest import drain


def _run(db, rid):
    with db.session() as s:
        return s.get(Run, rid)


def test_lifecycle_success(alice, worker, fake, db, store, settings):
    pid = alice.project()
    r = alice.start_run(pid, model_profile="best", max_usd=5)
    assert r["status"] == "queued" and r["model_profile"] == "best"
    assert drain(worker) == 1
    run = alice.get(f"/v1/runs/{r['id']}").json()
    assert run["status"] == "succeeded" and run["cost_usd"] == pytest.approx(0.4)
    assert run["completed_stages"] == ["ingest", "extract", "analyze"]
    assert run["engine_version"] == "fake-1.0" and run["started_at"] and run["finished_at"]
    # stages called in order, ingest got a source dir, analyze a budget
    assert [c[0] for c in fake.calls] == ["ingest", "extract", "analyze"]
    assert fake.calls[0][1]["source"] and fake.calls[2][1]["max_usd"] == pytest.approx(5.0)
    # usage + artifacts + results
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.run_id == r["id"])) == 4
        kinds = set(s.scalars(select(Artifact.kind).where(Artifact.project_id == pid)))
    assert {"state", "document", "graph", "graph_versions", "review_state"} <= kinds
    assert alice.get(f"/v1/projects/{pid}/document").text.startswith("# Understanding")
    u = alice.get(f"/v1/workspaces/{alice.wid}/usage").json()
    assert u["totals"]["usd"] == pytest.approx(0.4) and u["totals"]["input_tokens"] == 4000
    assert u["by_run"][0]["run_id"] == r["id"]
    ev = alice.get(f"/v1/workspaces/{alice.wid}/entitlements").json()
    assert ev["plan"] == "internal" and ev["consumption"]["runs_this_month"] == 1
    # scratch cleaned
    assert not list(Path(settings.scratch_dir).glob("*"))


def test_profile_to_models_and_key_injection(alice, worker, fake):
    pid = alice.project()
    alice.start_run(pid, model_profile="fast")
    drain(worker)
    cfg = fake.configs[-1]
    assert cfg["api_key"] == "sk-test-SECRET-KEY-123"
    assert set(cfg["models"].values()) == {"gemini/gemini-2.5-flash-lite"}
    alice.start_run(pid, model_profile="best", type="analyze", incremental=True)
    drain(worker)
    assert fake.configs[-1]["models"]["cross_ref"] == "gemini/gemini-2.5-pro"
    assert fake.configs[-1]["models"]["chunk_extract"] == "gemini/gemini-2.5-flash"


def test_secret_never_in_events_logs_or_responses(alice, worker, fake, db, caplog, store):
    caplog.set_level(logging.DEBUG)
    fake_log = lambda stage, pdir: None  # noqa: E731
    pid = alice.project()
    r = alice.start_run(pid)
    # an engine that (badly) echoes the key in a log event must still be scrubbed
    orig = fake._analyze

    def leaky(pdir, emit, max_usd, config):
        emit({"type": "log", "level": "info", "message": f"using key {config['api_key']}"})
        return orig(pdir, emit, max_usd, config)

    fake._analyze = leaky
    drain(worker)
    with db.session() as s:
        payloads = " ".join(str(e.payload) for e in s.scalars(select(RunEvent)))
    assert "SECRET-KEY" not in payloads and "[redacted]" in payloads
    assert "SECRET-KEY" not in caplog.text
    assert "SECRET-KEY" not in alice.get(f"/v1/runs/{r['id']}").text
    for p in Path(store.root).rglob("*"):
        if p.is_file() and p.suffix in (".json", ".md"):
            assert b"SECRET-KEY" not in p.read_bytes()


def test_source_content_not_logged(alice, worker, caplog):
    caplog.set_level(logging.DEBUG)
    pid = alice.project()
    alice.start_run(pid)
    drain(worker)
    assert "print('hi')" not in caplog.text


def test_budget_stop(alice, worker, fake):
    pid = alice.project()
    r = alice.start_run(pid, max_usd=0.25)  # 4 chunks x 0.10 -> stops after 2
    drain(worker)
    run = alice.get(f"/v1/runs/{r['id']}").json()
    assert run["status"] == "stopped_budget"
    assert run["cost_usd"] == pytest.approx(0.2) and run["cost_usd"] <= 0.25
    assert fake.calls[-1][1]["max_usd"] == pytest.approx(0.25)
    # partial results are still published
    assert alice.get(f"/v1/projects/{pid}/runs?status=stopped_budget").json()["total"] == 1


def test_monthly_cap_lowers_engine_budget(alice, worker, fake, db):
    from app.billing import ensure_entitlement
    pid = alice.project()
    r = alice.start_run(pid, max_usd=5)
    with db.session() as s:  # cap tightened after the run was accepted
        ensure_entitlement(s, alice.wid).overrides = {"monthly_usd_budget": 0.15}
    drain(worker)
    run = alice.get(f"/v1/runs/{r['id']}").json()
    assert fake.calls[-1][1]["max_usd"] == pytest.approx(0.15)
    assert run["status"] == "stopped_budget" and run["cost_usd"] == pytest.approx(0.1)


def test_unattributed_cost_is_metered(alice, worker, fake, db):
    fake.under_report_cost = True
    pid = alice.project()
    r = alice.start_run(pid)
    drain(worker)
    run = alice.get(f"/v1/runs/{r['id']}").json()
    assert run["cost_usd"] == pytest.approx(0.4)
    with db.session() as s:
        assert s.scalar(select(UsageEvent.task).where(UsageEvent.run_id == r["id"])) == "unattributed"


def test_entitlement_denial(alice, db):
    pid = alice.project()
    from app.billing import ensure_entitlement
    with db.session() as s:
        ensure_entitlement(s, alice.wid).overrides = {"max_usd_per_run": 1.0}
    r = alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 2})
    assert r.status_code == 403 and r.json()["error"]["code"] == "entitlement_denied"
    assert alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 0.5}).status_code == 202
    with db.session() as s:
        ensure_entitlement(s, alice.wid).overrides = {"max_concurrent_runs": 1}
    r = alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 0.5, "type": "verify"})
    assert r.status_code in (403, 409)


def test_entitlement_monthly_and_run_count(alice, worker, db):
    from app.billing import ensure_entitlement
    pid = alice.project()
    with db.session() as s:
        ensure_entitlement(s, alice.wid).overrides = {"max_runs_per_month": 1}
    assert alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 1}).status_code == 202
    drain(worker)
    r = alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 1})
    assert r.status_code == 403 and "monthly run limit" in r.json()["error"]["message"]
    with db.session() as s:
        ensure_entitlement(s, alice.wid).overrides = {"monthly_usd_budget": 0.5}
    assert alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 1}).status_code == 403


def test_estimate_endpoint(alice, fake, db):
    pid = alice.project()
    r = alice.post(f"/v1/projects/{pid}/estimate", json={"model_profile": "fast", "max_usd": 5})
    assert r.status_code == 200
    b = r.json()
    assert b == {"chunks": 4, "usd": 0.4, "allowed": True, "reason": ""}
    assert "api_key" not in fake.configs[-1]  # dry run never gets the credential
    assert alice.get(f"/v1/projects/{pid}").json()["estimate"]["usd"] == 0.4
    from app.billing import ensure_entitlement
    with db.session() as s:
        ensure_entitlement(s, alice.wid).overrides = {"max_usd_per_run": 0.1}
    b = alice.post(f"/v1/projects/{pid}/estimate", json={"max_usd": 5}).json()
    assert b["allowed"] is False and "per-run" in b["reason"]
    assert alice.post(f"/v1/projects/{pid}/estimate", json={"model_profile": "ultra"}).status_code == 422
    empty = alice.project("nosrc", with_source=False)
    assert alice.post(f"/v1/projects/{empty}/estimate", json={}).status_code == 409


def test_run_validation_and_preconditions(alice):
    empty = alice.project("nosrc", with_source=False)
    assert alice.post(f"/v1/projects/{empty}/runs", json={}).status_code == 409
    pid = alice.project()
    for body in ({"max_usd": 0}, {"max_usd": -1}, {"max_usd": 10**6}, {"type": "nuke"}, {"model_profile": "x"}):
        assert alice.post(f"/v1/projects/{pid}/runs", json=body).status_code == 422, body
    assert alice.post(f"/v1/projects/{pid}/runs", json={"type": "verify"}).status_code == 409  # nothing analysed yet
    assert alice.post(f"/v1/projects/{pid}/runs", json={"type": "reanalyze"}).status_code == 409


def test_idempotency_and_duplicate_active(alice):
    pid = alice.project()
    h = {"Idempotency-Key": "abc-1"}
    r1 = alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 1}, headers=h)
    r2 = alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 1}, headers=h)
    assert r1.status_code == 202 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    r3 = alice.post(f"/v1/projects/{pid}/runs", json={"max_usd": 1})
    assert r3.status_code == 409 and r3.json()["error"]["code"] == "run_in_progress"
    assert alice.get(f"/v1/projects/{pid}/runs").json()["total"] == 1


def test_cancel_queued_and_terminal(alice, worker):
    pid = alice.project()
    r = alice.start_run(pid)
    c = alice.post(f"/v1/runs/{r['id']}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert drain(worker) == 0  # job was cancelled, nothing to claim
    assert alice.post(f"/v1/runs/{r['id']}/cancel").status_code == 409


def test_cancel_running_stops_between_stages(alice, worker, fake):
    pid = alice.project()
    r = alice.start_run(pid)

    def hook(stage, pdir):
        if stage == "extract":
            assert alice.post(f"/v1/runs/{r['id']}/cancel").status_code == 200

    fake.on_stage_hook = hook
    drain(worker)
    run = alice.get(f"/v1/runs/{r['id']}").json()
    assert run["status"] == "cancelled" and "analyze" not in run["completed_stages"]


def test_transient_failure_retries_then_succeeds(alice, worker, fake, db):
    fake.fail["analyze"] = ["503 Service Unavailable from provider"]
    pid = alice.project()
    r = alice.start_run(pid)
    assert drain(worker) == 2  # same job claimed again after backoff (0s in tests)
    run = alice.get(f"/v1/runs/{r['id']}").json()
    assert run["status"] == "succeeded"
    assert [c[0] for c in fake.calls].count("ingest") == 1  # idempotent: ingest/extract not repeated on retry
    with db.session() as s:
        assert s.scalar(select(Job.attempts).where(Job.run_id == r["id"])) == 2
        types = [e.payload.get("status") for e in s.scalars(select(RunEvent).where(RunEvent.run_id == r["id"]).order_by(RunEvent.seq)) if e.type == "status"]
    assert "retrying" in types and types[-1] == "succeeded"


def test_crash_retries_exhaust_to_failed(alice, worker, fake):
    fake.crash["extract"] = [RuntimeError("boom")] * 5
    pid = alice.project()
    r = alice.start_run(pid)
    drain(worker, 10)
    run = alice.get(f"/v1/runs/{r['id']}").json()
    assert run["status"] == "failed" and run["error"]
    assert not list(Path(worker.st.scratch_dir).glob("*"))
    assert [c[0] for c in fake.calls].count("extract") == 3  # job_max_attempts


def test_permanent_failure_not_retried(alice, worker, fake):
    fake.fail["analyze"] = ["AuthenticationError: invalid api key sk-test-SECRET-KEY-123"]
    pid = alice.project()
    r = alice.start_run(pid)
    assert drain(worker) == 1
    run = alice.get(f"/v1/runs/{r['id']}").json()
    assert run["status"] == "failed" and "SECRET-KEY" not in run["error"]


def test_state_resumes_for_followup_runs(alice, worker, fake):
    pid = alice.project()
    alice.start_run(pid)
    drain(worker)
    alice.start_run(pid, type="verify")
    drain(worker)
    calls = [c[0] for c in fake.calls]
    assert calls[-1] == "verify"  # no ingest repeated: state came from the object store
    f = alice.get(f"/v1/projects/{pid}/findings").json()
    assert f["total"] == 2
    # same source again + analyze: ingest skipped because state already contains it
    alice.start_run(pid, incremental=True)
    drain(worker)
    assert calls.count("ingest") == 1 and [c[0] for c in fake.calls].count("ingest") == 1
    # new source -> refresh ingest with incremental=True
    from tests.conftest import make_zip
    alice.post(f"/v1/projects/{pid}/source", files={"file": ("n.zip", make_zip({"x.py": "1"}), "application/zip")})
    alice.start_run(pid)
    drain(worker)
    ing = [c for c in fake.calls if c[0] == "ingest"]
    assert len(ing) == 2 and ing[1][1]["incremental"] is True


def test_project_lock_serialises_runs(alice, worker, db):
    pid = alice.project()
    with db.session() as s:
        s.get(Project, pid).active_run_id = "someone-else"
    r = alice.start_run(pid)
    assert worker.run_once() is True  # claimed, found the lock, requeued
    assert alice.get(f"/v1/runs/{r['id']}").json()["status"] == "queued"
    with db.session() as s:
        s.get(Project, pid).active_run_id = None
        s.execute(Job.__table__.update().values(available_at=jobs.utcnow()))
    drain(worker)
    assert alice.get(f"/v1/runs/{r['id']}").json()["status"] == "succeeded"


def test_project_deleted_mid_run_leaves_nothing(alice, worker, fake, store, db):
    pid = alice.project()
    r = alice.start_run(pid)

    def hook(stage, pdir):
        if stage == "analyze":
            assert alice.delete(f"/v1/projects/{pid}").status_code == 204

    fake.on_stage_hook = hook
    drain(worker)
    assert store.list(f"{alice.wid}/") == []
    with db.session() as s:
        assert s.get(Run, r["id"]) is None
        assert s.scalar(select(func.count()).select_from(Artifact)) == 0
    assert not list(Path(worker.st.scratch_dir).glob("*"))


def test_stale_job_is_reaped_and_requeued(alice, db):
    pid = alice.project()
    r = alice.start_run(pid)
    with db.session() as s:
        j = jobs.claim(s, "dead-worker")
        j.locked_at = jobs.utcnow().replace(year=2020)
        s.commit()
        assert jobs.reap_stale(s, 60) == 1
        assert s.get(Job, j.id).status == "queued"
    assert alice.get(f"/v1/runs/{r['id']}").json()["status"] == "queued"


def test_no_double_claim_under_concurrency(alice, db):
    pid = alice.project()
    alice.start_run(pid)
    won = []

    def go(i):
        with db.session() as s:
            j = jobs.claim(s, f"w{i}")
            if j:
                won.append(i)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(won) == 1


def test_billing_seam_is_called_and_failures_ignored(alice, worker):
    from app import billing
    seen = []

    class Rec(billing.NoBilling):
        def report_usage(self, wid, usage):
            seen.append(usage)
            raise RuntimeError("provider down")

    billing.set_billing(Rec())
    try:
        pid = alice.project()
        r = alice.start_run(pid)
        drain(worker)
    finally:
        billing.set_billing(billing.NoBilling())
    assert alice.get(f"/v1/runs/{r['id']}").json()["status"] == "succeeded" and seen and seen[0]["run_id"] == r["id"]
