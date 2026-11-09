"""D-05: LLM timeouts / rate limits / outages / malformed replies handled gracefully; partial results preserved."""
import json

import httpx
import litellm
import pytest

from migration_agent.analyze.runner import run_analyze
from migration_agent.cli import main
from migration_agent.llm.base import BudgetExceeded, LLMAuthError, QuotaExhausted
from migration_agent.llm.litellm_client import LiteLLMClient, LLMConfig
from migration_agent.llm.offline import OfflineLLM
from migration_agent.pipeline import run_extract
from migration_agent.project import Project

REQ = httpx.Request("POST", "https://api.example.com/v1")


def http(status, headers=None):
    return httpx.Response(status, headers=headers or {}, request=REQ)


def timeout():
    return litellm.Timeout("request timed out", "claude-haiku-5-5", "anthropic")


def rate_limit(msg="rate limited", headers=None):
    return litellm.RateLimitError(msg, "anthropic", "claude-haiku-5-5", response=http(429, headers))


def unavailable(msg="The model is overloaded due to high demand, try again later"):
    return litellm.ServiceUnavailableError(msg, "gemini", "gemini-2.5-pro", response=http(503))


def ok(text="{}", model_note=""):
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class Transport:
    def __init__(self, seq):
        self.seq, self.calls = list(seq), []

    def __call__(self, **kw):
        self.calls.append(kw)
        item = self.seq.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def client(seq, **cfg):
    t = Transport(seq)
    sleeps = []
    return LiteLLMClient(LLMConfig(**cfg), transport=t, sleep=sleeps.append), t, sleeps


def call(c):
    return c.complete(task="chunk_extract", system="s", prompt="p")


# ---- client -------------------------------------------------------------------------------------
def test_retries_timeout_connection_and_5xx_with_exponential_backoff():
    conn = litellm.APIConnectionError("connection reset", "anthropic", "m")
    ise = litellm.InternalServerError("boom", "anthropic", "m", response=http(500))
    c, t, sleeps = client([timeout(), conn, ise, unavailable(), ok()], max_retries=5)
    assert call(c).text == "{}" and len(t.calls) == 5
    assert [s >= b for s, b in zip(sleeps, [1, 2, 4, 8])] == [True] * 4
    assert all(s <= 30 for s in sleeps)


def test_backoff_is_capped():
    c, t, sleeps = client([timeout()] * 9 + [ok()], max_retries=9, backoff_base=1, backoff_max=5)
    call(c)
    assert len(sleeps) == 9 and max(sleeps) <= 5


def test_retry_after_header_is_honoured_when_short():
    c, t, sleeps = client([rate_limit(headers={"retry-after": "7"}), ok()])
    call(c)
    assert 7 <= sleeps[0] <= 8


def test_provider_retry_delay_in_body_is_honoured():
    msg = 'RESOURCE_EXHAUSTED: {"error": {"details": [{"retryDelay": "34s"}]}}'
    c, t, sleeps = client([rate_limit(msg), ok()])
    call(c)
    assert 34 <= sleeps[0] <= 38


def test_huge_retry_delay_fails_fast_with_quota_exhausted():
    msg = 'quota exceeded for metric GenerateRequestsPerDay... "retryDelay": "81000s"'
    c, t, sleeps = client([rate_limit(msg), ok()])
    with pytest.raises(QuotaExhausted, match="quota exhausted"):
        call(c)
    assert sleeps == [] and len(t.calls) == 1
    assert issubclass(QuotaExhausted, BudgetExceeded)


def test_daily_quota_without_delay_hint_fails_fast():
    c, t, sleeps = client([rate_limit("You exceeded your current quota (insufficient_quota)"), ok()])
    with pytest.raises(QuotaExhausted):
        call(c)
    assert sleeps == []


def test_retries_exhausted_reraises_original_error():
    c, t, sleeps = client([unavailable()] * 10, max_retries=2)
    with pytest.raises(litellm.ServiceUnavailableError):
        call(c)
    assert len(t.calls) == 3 and len(sleeps) == 2


def test_fallback_model_used_after_overload_retries_exhausted():
    c, t, sleeps = client([unavailable()] * 3 + [ok('{"a":1}')], max_retries=2,
                          fallback_models={"claude-haiku-5-5": "claude-sonnet-5-5"})
    r = call(c)
    assert r.model == "claude-sonnet-5-5" and r.text == '{"a":1}'
    assert [k["model"] for k in t.calls] == ["claude-haiku-5-5"] * 3 + ["claude-sonnet-5-5"]
    # priced with the model that actually answered
    assert r.cost_usd == pytest.approx((10 * 3.0 + 5 * 15.0) / 1e6)


def test_fallback_not_used_for_non_retryable_and_not_cyclic():
    c, t, _ = client([litellm.BadRequestError("bad", "anthropic", "m", response=http(400)), ok()],
                     fallback_models={"claude-haiku-5-5": "claude-sonnet-5-5"})
    with pytest.raises(litellm.BadRequestError):
        call(c)
    assert len(t.calls) == 1
    c, t, _ = client([unavailable()] * 20, max_retries=0,
                     fallback_models={"claude-haiku-5-5": "claude-sonnet-5-5", "claude-sonnet-5-5": "claude-haiku-5-5"})
    with pytest.raises(litellm.ServiceUnavailableError):
        call(c)
    assert len(t.calls) == 2


def test_auth_errors_fail_immediately_with_clear_message():
    for e in (litellm.AuthenticationError("invalid x-api-key", "anthropic", "m", response=http(401)),
              litellm.PermissionDeniedError("forbidden", "anthropic", "m", response=http(403))):
        c, t, sleeps = client([e, ok()])
        with pytest.raises(LLMAuthError, match="API key"):
            call(c)
        assert len(t.calls) == 1 and sleeps == []


def test_not_found_and_bad_request_fail_immediately_with_hint():
    nf = litellm.NotFoundError("model not found", "anthropic", "claude-x", response=http(404))
    c, t, sleeps = client([nf, ok()])
    with pytest.raises(litellm.NotFoundError) as ei:
        call(c)
    assert len(t.calls) == 1 and sleeps == [] and any("not found" in n for n in ei.value.__notes__)
    br = litellm.ContextWindowExceededError("too long", "anthropic", "m", response=http(400))
    c, t, _ = client([br, ok()])
    with pytest.raises(litellm.ContextWindowExceededError):
        call(c)
    assert len(t.calls) == 1


def test_malformed_json_repair_once_and_empty_reply_handled():
    c, t, _ = client([ok("not json at all"), ok('{"fixed": true}')])
    assert json.loads(call(c).text) == {"fixed": True}
    assert len(t.calls) == 2 and t.calls[1]["messages"][-1]["content"].startswith("Your previous reply")
    c, t, _ = client([{"choices": [], "usage": None}, ok("{}")])     # empty provider reply -> repair flow, no crash
    assert call(c).text == "{}"


def test_repair_call_can_also_fall_back():
    c, t, _ = client([ok("garbage"), unavailable(), ok("{}")], max_retries=0,
                     fallback_models={"claude-haiku-5-5": "claude-sonnet-5-5"})
    r = call(c)
    assert r.text == "{}" and r.model == "claude-sonnet-5-5"


def test_config_loads_fallbacks(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"llm": {"fallback_models": {"a": "b"}, "max_retry_after": 30}}))
    cfg = LLMConfig.load(p)
    assert cfg.fallback_models == {"a": "b"} and cfg.max_retry_after == 30


# ---- runner -------------------------------------------------------------------------------------
CTX = 14_000


def body(name):
    return "".join(f"# {name} filler {j}\n" for j in range(30)) + f"def {name}():\n    return 1\n"


def make(tmp_path):
    proj = Project.init(tmp_path / "p.migration", "src", "p")
    for d in "abcd":
        (proj.code / d).mkdir(parents=True)
        (proj.code / d / f"{d}.py").write_text(body(d))
    run_extract(proj)
    return proj


class Flaky(OfflineLLM):
    """Raises `exc` for chunk_extract calls whose prompt mentions one of `bad` chunk ids."""

    def __init__(self, bad=(), exc=None, tasks=("chunk_extract",)):
        super().__init__()
        self.bad, self.exc, self.tasks = set(bad), exc, tasks

    def complete(self, **kw):
        if kw["task"] in self.tasks and any(f"{b}" in kw["prompt"] for b in self.bad):
            raise self.exc() if callable(self.exc) else self.exc
        return super().complete(**kw)


def test_failed_chunk_is_recorded_run_continues_and_rerun_retries_only_it(tmp_path):
    proj = make(tmp_path)
    llm = Flaky(bad=["CH-0002"], exc=lambda: unavailable())
    out = run_analyze(proj, llm, context_tokens=CTX)
    assert out["chunks"] == 4 and out["failed_chunks"] == 1 and out["failed"] == ["CH-0002"] and out["stopped"] is None
    f = json.loads((proj.root / "checkpoint" / "failures.json").read_text())
    assert f["CH-0002"]["task"] == "chunk_extract" and f["CH-0002"]["error_class"] == "ServiceUnavailableError"
    assert (proj.docs / "understanding.latest.md").exists() and (proj.docs / "graph.html").exists()
    assert out["nodes"] >= 3                                           # other chunks' results preserved
    # re-run: only the failed chunk is retried
    llm2 = OfflineLLM()
    out2 = run_analyze(proj, llm2, context_tokens=CTX)
    assert len(llm2.calls) == 2 and out2["failed_chunks"] == 0 and out2["reused_chunks"] == 3
    assert json.loads((proj.root / "checkpoint" / "failures.json").read_text()) == {}
    assert {n.name for n in __import__("migration_agent.graph", fromlist=["x"]).load_latest(proj.graph_dir).nodes} >= {"b/b.py"}


def test_business_rules_failure_keeps_the_extract_result(tmp_path):
    proj = make(tmp_path)
    llm = Flaky(bad=["CH-0001"], exc=lambda: timeout(), tasks=("business_rules",))
    out = run_analyze(proj, llm, context_tokens=CTX)
    f = json.loads((proj.root / "checkpoint" / "failures.json").read_text())
    assert out["failed_chunks"] == 1 and f["CH-0001"]["task"] == "business_rules"
    assert out["nodes"] >= 4


def test_malformed_output_twice_is_a_recorded_failure_not_a_crash(tmp_path):
    proj = make(tmp_path)

    class Garbage(OfflineLLM):
        def complete(self, **kw):
            if kw["task"] == "chunk_extract" and "CH-0003" in kw["prompt"]:
                from migration_agent.llm.base import LLMResponse
                return LLMResponse(text="[1,2,", model="x")
            return super().complete(**kw)

    out = run_analyze(proj, Garbage(), context_tokens=CTX)
    f = json.loads((proj.root / "checkpoint" / "failures.json").read_text())
    assert out["failed"] == ["CH-0003"] and f["CH-0003"]["error_class"] in ("JSONDecodeError", "AnalysisError")


def test_quota_exhausted_stops_gracefully_with_partial_results(tmp_path):
    proj = make(tmp_path)
    llm = Flaky(bad=["CH-0003"], exc=lambda: QuotaExhausted("quota exhausted for model x: wait 81000s"))
    out = run_analyze(proj, llm, context_tokens=CTX)
    assert out["stopped"] and "quota" in out["stopped"] and out["nodes"] >= 2
    assert (proj.docs / "understanding.latest.md").exists()
    out2 = run_analyze(proj, OfflineLLM(), context_tokens=CTX)       # next day: resumes the unfinished chunks
    assert out2["stopped"] is None and out2["reused_chunks"] == 2 and out2["analyzed_chunks"] == 2


def test_auth_error_stops_the_run_instead_of_failing_every_chunk(tmp_path):
    proj = make(tmp_path)
    llm = Flaky(bad=["CH-0001"], exc=lambda: LLMAuthError("authentication failed"))
    out = run_analyze(proj, llm, context_tokens=CTX)
    assert out["stopped"] and "authentication" in out["stopped"] and out["failed_chunks"] == 0
    assert (proj.docs / "understanding.latest.md").exists()


def test_failures_with_workers_do_not_lose_other_chunks(tmp_path):
    proj = make(tmp_path)
    out = run_analyze(proj, Flaky(bad=["CH-0002"], exc=lambda: timeout()), context_tokens=CTX, workers=3)
    assert out["failed"] == ["CH-0002"] and out["nodes"] >= 3


def test_cross_ref_failure_does_not_crash_the_run(tmp_path, monkeypatch):
    proj = make(tmp_path)
    from migration_agent.analyze import runner
    from migration_agent.analyze.patch import NodeRef, UnresolvedRef
    from migration_agent.analyze.merger import MergeResult, merge_patches as real

    def fake_merge(patches, reg, base=None):
        r = real(patches, reg, base=base)
        if not any(p.chunk_id == "cross_ref" for p in patches):
            r.unresolved.append(UnresolvedRef(from_node=NodeRef(type="component", qualified_symbol="a/a.py"), name="zzz"))
        return r

    def boom(*a, **k):
        raise unavailable()

    monkeypatch.setattr(runner, "merge_patches", fake_merge)
    monkeypatch.setattr(runner, "resolve_cross_refs", boom)
    out = run_analyze(proj, OfflineLLM(), context_tokens=CTX)
    assert out["failed_tasks"] == ["cross_ref"] and out["nodes"] >= 4
    assert (proj.docs / "understanding.latest.md").exists()


def test_cli_reports_failures(tmp_path, monkeypatch):
    from click.testing import CliRunner
    proj = make(tmp_path)
    from migration_agent.analyze import runner
    real = runner.run_analyze
    monkeypatch.setattr(runner, "run_analyze",
                        lambda p, llm, **kw: real(p, Flaky(bad=["CH-0001"], exc=lambda: timeout()), **{**kw, "context_tokens": CTX}))
    res = CliRunner().invoke(main, ["analyze", str(proj.root), "--offline"])
    assert res.exit_code == 0, res.output
    assert "failed" in res.output and "failures.json" in res.output and "cost: this session" in res.output
