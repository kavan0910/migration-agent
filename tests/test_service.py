"""Engine service facade (platform contract section 2). Offline: no network, no real API calls, no spend."""
import gzip
import io
import json
import tarfile
import threading
from pathlib import Path

import pytest

from migration_agent import service
from migration_agent.cli import main
from migration_agent.graph import load_latest
from migration_agent.llm.base import FakeLLM, LLMAuthError, LLMResponse
from migration_agent.llm.offline import OfflineLLM
from migration_agent.storage import ArchiveError, pack_project, unpack_project
from migration_agent.verify.models import Finding, FindingsFile

REPO = Path(__file__).parent.parent / "eval" / "repos" / "express-crud"


class CostLLM(OfflineLLM):
    """OfflineLLM that reports a price per call."""

    def __init__(self, usd=1.0):
        super().__init__()
        self.usd = usd

    def complete(self, **kw):
        r = super().complete(**kw)
        return r.model_copy(update={"cost_usd": self.usd})


def _real_client(monkeypatch):
    """tests/e2e installs a replay stand-in for LiteLLMClient that may still be active; use the real class."""
    import migration_agent.llm.litellm_client as lc
    monkeypatch.setattr(lc, "LiteLLMClient", getattr(lc, "_RealClient", lc.LiteLLMClient))
    return lc


def _events():
    evs, lock = [], threading.Lock()

    def cb(e):
        with lock:
            evs.append(e)
    return evs, cb


@pytest.fixture()
def ingested(tmp_path):
    root = tmp_path / "p.migration"
    r = service.run_stage(root, "ingest", source=str(REPO))
    assert r.status == "ok", r.error
    return root


@pytest.fixture()
def analyzed(ingested):
    assert service.run_stage(ingested, "extract").status == "ok"
    r = service.run_stage(ingested, "analyze", offline=True)
    assert r.status == "ok", r.error
    return ingested


# ------------------------------------------------------------------ stages
def test_ingest_creates_project_and_report(ingested):
    assert (ingested / "project.json").exists() and (ingested / "ingest_report.json").exists()
    assert (ingested / "code" / "package.json").exists()
    assert service.run_stage(ingested, "ingest").status == "failed"       # code/ not empty: error as result, no raise


def test_extract_summary(ingested):
    r = service.run_stage(ingested, "extract")
    assert r.status == "ok" and r.summary["items"] > 0 and r.summary["counts"]["source_file"] > 0
    assert (ingested / "inventory.json").exists() and r.cost_usd == 0


def test_analyze_offline_matches_cli_and_emits_events(ingested):
    evs, cb = _events()
    r = service.run_stage(ingested, "analyze", offline=True, on_event=cb)
    assert r.status == "ok" and r.summary["coverage_complete"] is True and r.summary["nodes"] > 0
    assert (ingested / "docs" / "understanding.latest.md").exists()
    prog = [e for e in evs if e["type"] == "progress"]
    assert prog and prog[-1]["done"] == prog[-1]["total"] == r.summary["chunks"]
    assert all(e["stage"] == "analyze" for e in prog)
    assert [e["done"] for e in prog] == sorted(e["done"] for e in prog)
    usage = [e for e in evs if e["type"] == "usage"]
    assert usage and {"stage", "task", "model", "input_tokens", "output_tokens", "usd"} <= set(usage[0])
    assert {e["task"] for e in usage} >= {"chunk_extract", "business_rules"}
    assert any(e["type"] == "log" for e in evs)
    # graph identical to what the CLI produces for the same project
    from click.testing import CliRunner
    other = ingested.parent / "q.migration"
    cr = CliRunner()
    for a in (["init", str(REPO), "--project", str(other)], ["ingest", str(other)], ["analyze", str(other), "--offline"]):
        assert cr.invoke(main, a).exit_code == 0
    ids = lambda root: sorted(n.id for n in load_latest(root / "graph").nodes)  # noqa: E731
    assert ids(ingested) == ids(other)


def test_analyze_without_ingest_fails_cleanly(tmp_path):
    r = service.run_stage(tmp_path / "missing", "analyze", offline=True)
    assert r.status == "failed" and "not a project" in r.error


def test_unknown_stage_raises(tmp_path):
    with pytest.raises(service.ServiceError):
        service.run_stage(tmp_path, "bogus")


def test_events_threadsafe_with_workers_and_bad_callback(ingested):
    evs, lock = [], threading.Lock()
    n = {"c": 0}

    def cb(e):
        with lock:
            n["c"] += 1
            evs.append(e)
        if n["c"] == 2:
            raise RuntimeError("callback bug")        # must not break the run
    r = service.run_stage(ingested, "analyze", offline=True, workers=4, on_event=cb)
    assert r.status == "ok"
    assert len([e for e in evs if e["type"] == "usage"]) == r.summary["llm_calls"]


def test_usage_events_carry_cost_and_sum_to_result(ingested):
    evs, cb = _events()
    r = service.run_stage(ingested, "analyze", llm=CostLLM(0.01), on_event=cb)
    assert r.status == "ok"
    total = sum(e["usd"] for e in evs if e["type"] == "usage")
    assert abs(total - r.cost_usd) < 1e-9 and r.cost_usd > 0
    assert abs(r.cost_usd - r.summary["session_cost_usd"]) < 1e-6


def test_budget_stop_is_graceful(ingested):
    r = service.run_stage(ingested, "analyze", llm=CostLLM(1.0), max_usd=0.5)
    assert r.status == "stopped_budget" and "budget" in r.error.lower()
    assert r.cost_usd >= 1.0
    assert (ingested / "docs" / "understanding.latest.md").exists()      # partial results still rendered


def test_budget_from_config_and_exception_path(ingested):
    def boom(task, system, prompt):
        from migration_agent.llm.base import BudgetExceeded
        raise BudgetExceeded("budget $0.00 reached")
    r = service.run_stage(ingested, "analyze", llm=FakeLLM(boom), config={"max_usd": 0.0})
    assert r.status == "stopped_budget"


def test_auth_error_is_failed_not_budget(ingested):
    def boom(task, system, prompt):
        raise LLMAuthError("bad key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    key = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    evs, cb = _events()
    r = service.run_stage(ingested, "analyze", llm=FakeLLM(boom), config={"api_key": key}, on_event=cb)
    assert r.status == "failed" and "bad key" in r.error
    assert key not in r.error and key not in json.dumps(evs)


def test_estimate(ingested):
    est = service.estimate(ingested)
    assert est["chunks"] > 0 and est["usd"] > 0
    est2 = service.estimate(ingested, config={"pricing": {"claude-haiku-5-5": [0, 0]}, "default_pricing": [0, 0],
                                              "models": {"chunk_extract": "claude-haiku-5-5", "business_rules": "claude-haiku-5-5"}})
    assert est2["usd"] == 0


def _verifier_llm(findings=()):
    def r(task, system, prompt):
        if "Document source" in prompt:
            return json.dumps({"claims": [{"text": "c", "quote": "Invoices are due in 30 days."}]})
        return json.dumps({"findings": list(findings)})
    return FakeLLM(r)


def test_verify_stage(analyzed):
    r = service.run_stage(analyzed, "verify", llm=_verifier_llm())
    assert r.status == "ok" and r.summary["findings"] == 0        # no user_docs: nothing to compare
    (analyzed / "user_docs" / "spec.md").write_text("# Spec\nInvoices are due in 30 days.\n")
    evs, cb = _events()
    r = service.run_stage(analyzed, "verify", llm=_verifier_llm([{"kind": "doc_only", "title": "t", "quotes": [
        {"source": "spec.md", "text": "Invoices are due in 30 days."}]}]), on_event=cb)
    assert r.status == "ok" and r.summary["findings"] == 1
    assert any(e["type"] == "usage" and e["stage"] == "verify" and e["task"] == "verifier" for e in evs)
    assert (analyzed / "feedback" / "findings.json").exists()


def test_verify_needs_doc_and_budget(ingested, analyzed):
    assert service.run_stage(ingested.parent / "none", "verify").status == "failed"
    (analyzed / "user_docs" / "spec.md").write_text("# Spec\nInvoices are due in 30 days.\n")
    r = service.run_stage(analyzed, "verify", llm=CostLLM(1.0), max_usd=0.5)
    assert r.status == "stopped_budget"


def test_verify_without_doc_fails(ingested):
    r = service.run_stage(ingested, "verify", llm=_verifier_llm())
    assert r.status == "failed" and "analyze" in r.error


# ------------------------------------------------------------------ api key / config
def test_explicit_api_key_used_without_env(ingested, monkeypatch):
    lc = _real_client(monkeypatch)
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "MA_LLM_MODEL", "MA_MAX_USD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MA_LLM_MODEL_CHUNK_EXTRACT", "env-model-must-be-ignored")
    seen = []

    def transport(**kw):
        seen.append(kw)
        text = OfflineLLM()._chunk_extract(kw["messages"][-1]["content"])
        return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 100, "completion_tokens": 50}}
    monkeypatch.setattr(lc, "_default_transport", lambda: transport)
    cfg = {"api_key": "sk-test-explicit-1234", "models": {"chunk_extract": "m-a", "business_rules": "m-b"},
           "default_model": "m-b", "pricing": {"m-a": [1.0, 2.0], "m-b": [1.0, 2.0]}}
    evs, cb = _events()
    r = service.run_stage(ingested, "analyze", config=cfg, max_usd=5.0, on_event=cb)
    assert r.status == "ok", r.error
    assert seen and all(k["api_key"] == "sk-test-explicit-1234" for k in seen)
    assert {k["model"] for k in seen} <= {"m-a", "m-b"}
    assert r.cost_usd > 0 and {e["model"] for e in evs if e["type"] == "usage"} <= {"m-a", "m-b"}
    blob = "".join(p.read_text(errors="ignore") for p in ingested.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl", ".md"))
    assert "sk-test-explicit-1234" not in blob                      # never persisted
    assert "sk-test-explicit-1234" not in repr(service._llm_config(cfg))


def test_llm_cache_second_run_is_free(ingested, monkeypatch):
    lc = _real_client(monkeypatch)
    calls = []

    def transport(**kw):
        calls.append(1)
        return {"choices": [{"message": {"content": OfflineLLM()._chunk_extract(kw["messages"][-1]["content"])}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    monkeypatch.setattr(lc, "_default_transport", lambda: transport)
    cfg = {"api_key": "k"}
    assert service.run_stage(ingested, "analyze", config=cfg).status == "ok"
    first = len(calls)
    import shutil
    shutil.rmtree(ingested / "checkpoint")
    assert service.run_stage(ingested, "analyze", config=cfg).cost_usd == 0 and len(calls) == first


# ------------------------------------------------------------------ review / graph / resolve
def _with_finding(root):
    g = load_latest(root / "graph")
    comp = next(n for n in g.nodes if n.type == "component" and n.evidence and n.evidence[0].line_start)
    ff = FindingsFile(graph_version=g.version, findings=[
        Finding(id="F-0001", kind="contradiction", title="t", item_ids=[comp.id])])
    (root / "feedback" / "findings.json").write_text(ff.model_dump_json())
    return comp


def test_review_round_trip(analyzed):
    comp = _with_finding(analyzed)
    st = service.review_state(analyzed)
    assert {"project", "findings", "resolutions", "comments", "versions", "sections", "coverage", "known_ids", "errors"} <= set(st)
    assert st["findings"]["findings"][0]["id"] == "F-0001" and comp.id in st["known_ids"]

    c = service.add_comment(analyzed, comp.id, "why? api_key=ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ", author="bob")
    assert c["id"] == "CM-0001" and c["author"] == "bob" and "ZZZZZZZZ" not in c["text"]
    assert service.add_comment(analyzed, comp.id, "again")["id"] == "CM-0002"
    r = service.add_resolution(analyzed, "F-0001", "needs_reanalysis", "check it")
    assert r["finding_id"] == "F-0001" and r["status"] == "needs_reanalysis"

    st = service.review_state(analyzed)
    assert [x["id"] for x in st["comments"]["comments"]] == ["CM-0001", "CM-0002"]
    assert st["resolutions"]["resolutions"][0]["note"] == "check it"

    for bad in (lambda: service.add_comment(analyzed, "COMP-9999", "x"),
                lambda: service.add_comment(analyzed, comp.id, ""),
                lambda: service.add_resolution(analyzed, "F-9999", "dismissed"),
                lambda: service.add_resolution(analyzed, "F-0001", "nonsense")):
        with pytest.raises(service.ServiceError):
            bad()
    with pytest.raises(service.ServiceError) as ei:
        service.add_comment(analyzed, "COMP-9999", "x")
    assert ei.value.code == "not_found"


def test_review_concurrent_comments_get_unique_ids(analyzed):
    comp = _with_finding(analyzed)
    ts = [threading.Thread(target=service.add_comment, args=(analyzed, comp.id, f"c{i}")) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = [c["id"] for c in service.review_state(analyzed)["comments"]["comments"]]
    assert len(ids) == len(set(ids)) == 12


def test_resolve_stage_reanalysis_changelog_and_graph_versions(analyzed):
    comp = _with_finding(analyzed)
    ev = comp.evidence[0]
    line = (REPO / ev.file).read_text().splitlines()[ev.line_start - 1].strip()

    def responder(task, system, prompt):
        return json.dumps({"chunk_id": "x", "edges": [], "unresolved": [], "nodes": [
            {"type": comp.type, "module": comp.module, "qualified_symbol": comp.qualified_symbol, "name": comp.name,
             "responsibility": "REVISED", "evidence": [{"file": ev.file, "line_start": ev.line_start,
                                                        "line_end": ev.line_start, "quote": line}]}]})
    v0 = service.graph_versions(analyzed)
    assert [v["version"] for v in v0] == [1] or len(v0) >= 1
    service.add_comment(analyzed, comp.id, "recheck")
    # record only (reanalyze False)
    r = service.run_stage(analyzed, "resolve", finding_id="F-0001", status="needs_reanalysis", note="n")
    assert r.status == "ok" and r.summary["resolution"]["status"] == "needs_reanalysis" and r.cost_usd == 0
    evs, cb = _events()
    r = service.run_stage(analyzed, "resolve", reanalyze=True, llm=FakeLLM(responder), on_event=cb)
    assert r.status == "ok" and r.summary["new_version"] == r.summary["old_version"] + 1
    assert any(e["type"] == "usage" and e["stage"] == "resolve" for e in evs)
    assert load_latest(analyzed / "graph").node(comp.id).responsibility == "REVISED"
    old_v, new_v = r.summary["old_version"], r.summary["new_version"]
    assert [v["version"] for v in service.graph_versions(analyzed)][-2:] == [old_v, new_v]
    assert service.graph_snapshot(analyzed)["version"] == new_v
    snap = service.graph_snapshot(analyzed, old_v)
    assert snap["version"] == old_v and snap["nodes"] and "edges" in snap
    d = service.graph_diff(analyzed, old_v, new_v)
    assert d["a"] == old_v and any(x["id"] == comp.id for x in d["changed_nodes"])
    text = service.changelog(analyzed)
    assert f"v{new_v}" in text and "F-0001" in text
    assert "REVISED" in service.document(analyzed)
    with pytest.raises(service.ServiceError):
        service.graph_snapshot(analyzed, 999)
    with pytest.raises(service.ServiceError):
        service.graph_diff(analyzed, 1, 999)
    # nothing pending now
    again = service.run_stage(analyzed, "resolve", reanalyze=True, llm=FakeLLM(responder))
    assert again.status == "ok" and again.summary.get("nothing_to_reanalyze")


def test_resolve_unknown_finding_and_budget(analyzed):
    comp = _with_finding(analyzed)
    r = service.run_stage(analyzed, "resolve", finding_id="F-9999", status="dismissed")
    assert r.status == "failed" and "F-9999" in r.error
    r = service.run_stage(analyzed, "resolve", finding_id="F-0001")
    assert r.status == "failed"
    service.add_comment(analyzed, comp.id, "x")
    before = load_latest(analyzed / "graph").version
    r = service.run_stage(analyzed, "resolve", reanalyze=True, llm=CostLLM(1.0), max_usd=0.0)
    assert r.status == "stopped_budget" and load_latest(analyzed / "graph").version == before


def test_graph_functions_on_empty_project(tmp_path):
    from migration_agent.project import Project
    Project.init(tmp_path / "e", "x")
    assert service.graph_snapshot(tmp_path / "e") == {"version": 0, "nodes": [], "edges": []}
    assert service.graph_versions(tmp_path / "e") == []
    assert service.document(tmp_path / "e") == ""


# ------------------------------------------------------------------ pack / unpack
def test_pack_deterministic_roundtrip_and_cache_flag(analyzed):
    (analyzed / "llm_cache").mkdir(exist_ok=True)
    (analyzed / "llm_cache" / "abc.json").write_text("{}")
    a, b = pack_project(analyzed), pack_project(analyzed)
    assert a == b and a[:2] == b"\x1f\x8b"
    names = lambda blob: {m.name for m in tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz").getmembers()}  # noqa: E731
    assert "llm_cache/abc.json" in names(a)
    nc = pack_project(analyzed, include_cache=False)
    assert not any(n.startswith("llm_cache") for n in names(nc))
    for m in tarfile.open(fileobj=io.BytesIO(a), mode="r:gz").getmembers():
        assert m.mtime == 0 and m.uid == 0 and m.gid == 0

    dest = analyzed.parent / "restored"
    unpack_project(a, dest)
    rel = lambda root: {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}  # noqa: E731
    assert rel(dest) == rel(analyzed)
    assert pack_project(dest) == a                                    # byte-identical after round trip
    assert service.review_state(dest)["project"]
    assert service.estimate(dest)["chunks"] > 0


def test_pack_skips_symlinks(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.txt").write_text("x")
    (root / "link").symlink_to("a.txt")
    names = {m.name for m in tarfile.open(fileobj=io.BytesIO(pack_project(root)), mode="r:gz").getmembers()}
    assert names == {"a.txt"}


def _tar(members):
    """members: list of (TarInfo, bytes|None)"""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tf:
        for ti, data in members:
            tf.addfile(ti, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _file(name, data=b"x"):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    return ti, data


def _special(name, typ, link=""):
    ti = tarfile.TarInfo(name)
    ti.type, ti.linkname = typ, link
    return ti, None


@pytest.mark.parametrize("members", [
    [_file("../evil.txt")],
    [_file("a/../../evil.txt")],
    [_file("/etc/passwd")],
    [_file("C:/x.txt")],
    [_file("a\\b.txt")],
    [_special("link", tarfile.SYMTYPE, "/etc/passwd")],
    [_special("link", tarfile.SYMTYPE, "a")],
    [_special("hard", tarfile.LNKTYPE, "a")],
    [_special("dev", tarfile.CHRTYPE)],
    [_special("blk", tarfile.BLKTYPE)],
    [_special("fifo", tarfile.FIFOTYPE)],
    [_file("ok.txt"), _file("ok.txt")],
    [_file("ok.txt"), _file("ok.txt/inner")],
])
def test_unpack_rejects_unsafe(tmp_path, members):
    dest = tmp_path / "d"
    with pytest.raises(ArchiveError):
        unpack_project(_tar(members), dest)
    assert not dest.exists()                                           # cleaned up
    assert not (tmp_path / "evil.txt").exists()
    assert not list(tmp_path.glob("*evil*"))


def test_unpack_symlink_escape_chain_rejected(tmp_path):
    dest = tmp_path / "d"
    blob = _tar([_special("s", tarfile.SYMTYPE, ".."), _file("s/evil.txt")])
    with pytest.raises(ArchiveError):
        unpack_project(blob, dest)
    assert not (tmp_path / "evil.txt").exists()


def test_unpack_size_caps_and_garbage(tmp_path):
    big = _tar([_file("a.bin", b"\0" * 5000), _file("b.bin", b"\0" * 5000)])
    with pytest.raises(ArchiveError, match="size"):
        unpack_project(big, tmp_path / "d1", max_unpacked_bytes=8000)
    assert not (tmp_path / "d1").exists()
    with pytest.raises(ArchiveError, match="entries"):
        unpack_project(_tar([_file(f"f{i}") for i in range(5)]), tmp_path / "d2", max_members=3)
    with pytest.raises(ArchiveError, match="larger"):
        unpack_project(big, tmp_path / "d3", max_archive_bytes=10)
    with pytest.raises(ArchiveError):
        unpack_project(b"not a tarball", tmp_path / "d4")
    with pytest.raises(ArchiveError):
        unpack_project(gzip.compress(b"x" * 1000), tmp_path / "d5")


def test_unpack_dest_must_be_empty_and_is_not_clobbered(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "keep.txt").write_text("keep")
    with pytest.raises(ArchiveError):
        unpack_project(_tar([_file("a")]), dest)
    assert (dest / "keep.txt").read_text() == "keep"
    empty = tmp_path / "e"
    empty.mkdir()
    unpack_project(_tar([_file("./sub/a.txt", b"hi")]), empty)
    assert (empty / "sub" / "a.txt").read_text() == "hi"
    # failure into a pre-existing empty dir leaves it empty again
    empty2 = tmp_path / "e2"
    empty2.mkdir()
    with pytest.raises(ArchiveError):
        unpack_project(_tar([_file("ok"), _file("../x")]), empty2)
    assert empty2.is_dir() and not list(empty2.iterdir())


def test_cli_unchanged_smoke(tmp_path):
    from click.testing import CliRunner
    root = tmp_path / "c.migration"
    cr = CliRunner()
    for a in (["init", str(REPO), "--project", str(root)], ["ingest", str(root)], ["extract", str(root)],
              ["analyze", str(root), "--offline"], ["changelog", str(root)]):
        res = cr.invoke(main, a)
        assert res.exit_code == 0, res.output
    assert "ingested" in cr.invoke(main, ["ingest", str(root), "--refresh"]).output
