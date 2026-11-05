import json
import multiprocessing as mp

import pytest

from migration_agent.audit import AuditLog, read_audit, render_audit


def test_record_and_seq(tmp_path):
    log = AuditLog(tmp_path / "a" / "audit.jsonl")
    a = log.record("scout", "scan", command="understand", tokens_in=10, tokens_out=5, cost_usd=0.01, foo="bar")
    b = log.record("scout", "scan")
    assert (a["seq"], b["seq"]) == (1, 2)
    rows = read_audit(log.path)
    assert rows[0]["foo"] == "bar" and rows[0]["ts"]
    assert AuditLog(log.path).record("x", "y")["seq"] == 3  # continues across instances


def _worker(path):
    log = AuditLog(path)
    for i in range(25):
        log.record("w", "a", i=i)


def test_multiprocess_appends(tmp_path):
    p = tmp_path / "audit.jsonl"
    ps = [mp.Process(target=_worker, args=(p,)) for _ in range(4)]
    [x.start() for x in ps]
    [x.join() for x in ps]
    rows = read_audit(p)
    assert len(rows) == 100
    assert sorted(r["seq"] for r in rows) == list(range(1, 101))


def test_span_ok_and_error(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    with log.span("a", "ok") as s:
        s["model"] = "m"
    with pytest.raises(ValueError):
        with log.span("a", "bad"):
            raise ValueError("boom")
    ok, bad = read_audit(log.path)
    assert ok["status"] == "ok" and ok["model"] == "m" and ok["duration_ms"] >= 0
    assert bad["status"] == "error" and "boom" in bad["error"]


def test_read_missing(tmp_path):
    assert read_audit(tmp_path / "none.jsonl") == []


def test_render(tmp_path):
    log = AuditLog(tmp_path / "a.jsonl")
    log.record("a", "x", command="c1", tokens_in=100, tokens_out=50, cost_usd=0.5)
    log.record("a", "y", command="c2", tokens_in=1, tokens_out=2, cost_usd=0.25)
    out = render_audit(log.path)
    assert "tokens in/out 101/52" in out and "$0.7500" in out and "2 commands" in out
    j = json.loads(render_audit(log.path, "json"))
    assert j["totals"]["entries"] == 2
    with pytest.raises(ValueError):
        render_audit(log.path, "xml")
    assert "totals" in render_audit(tmp_path / "empty.jsonl")
