import json

import pytest

from migration_agent.audit import AuditLog, read_audit
from migration_agent.llm.base import BudgetExceeded
from migration_agent.llm.litellm_client import Budget, LiteLLMClient, LLMConfig, estimate_cost


class Err(Exception):
    def __init__(self, status):
        self.status_code = status


def resp(text, tin=100, tout=50):
    return {"choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": tin, "completion_tokens": tout}}


class Transport:
    def __init__(self, seq):
        self.seq, self.calls = list(seq), []

    def __call__(self, **kw):
        self.calls.append(kw)
        item = self.seq.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def client(seq, **kw):
    t = Transport(seq)
    sleeps = []
    c = LiteLLMClient(transport=t, sleep=sleeps.append, **kw)
    return c, t, sleeps


def test_tiers_and_cost_and_cache_control():
    c, t, _ = client([resp('{"a":1}', 1000, 500), resp("{}", 1000, 500)])
    r = c.complete(task="chunk_extract", system="S", prompt="P")
    assert r.model == "claude-haiku-5-5"
    assert r.cost_usd == pytest.approx((1000 * 1.0 + 500 * 5.0) / 1e6)
    assert t.calls[0]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert c.complete(task="verifier", system="S", prompt="P").model == "claude-sonnet-5-5"


def test_retry_sequence_with_backoff():
    c, t, sleeps = client([Err(429), TimeoutError(), Err(503), resp("{}")])
    r = c.complete(task="cross_ref", system="s", prompt="p")
    assert r.text == "{}" and len(t.calls) == 4
    assert len(sleeps) == 3
    assert sleeps[0] >= 1 and sleeps[1] >= 2 and sleeps[2] >= 4  # exponential (+jitter)


def test_non_retryable_and_exhausted():
    c, t, sleeps = client([Err(400)])
    with pytest.raises(Err):
        c.complete(task="cross_ref", system="s", prompt="p")
    assert sleeps == []
    c, t, sleeps = client([Err(500)] * 10, config=LLMConfig(max_retries=2))
    with pytest.raises(Err):
        c.complete(task="cross_ref", system="s", prompt="p")
    assert len(t.calls) == 3 and len(sleeps) == 2


def test_malformed_json_retry_once():
    c, t, _ = client([resp("not json at all", 10, 5), resp('{"ok":true}', 10, 5)])
    r = c.complete(task="gap_fill", system="s", prompt="p")
    assert json.loads(r.text) == {"ok": True}
    assert len(t.calls) == 2
    assert "valid JSON" in t.calls[1]["messages"][-1]["content"]
    assert r.tokens_in == 20 and r.tokens_out == 10
    # still malformed -> only one retry, returns raw text
    c, t, _ = client([resp("bad"), resp("still bad")])
    assert c.complete(task="gap_fill", system="s", prompt="p").text == "still bad"
    assert len(t.calls) == 2


def test_budget_stops_before_call():
    b = Budget(max_usd=0.001)
    c, t, _ = client([resp("{}")], budget=b, config=LLMConfig(max_output_tokens=4096))
    with pytest.raises(BudgetExceeded):
        c.complete(task="verifier", system="s", prompt="p")
    assert t.calls == []
    b2 = Budget(max_tokens=100)
    c, t, _ = client([resp("{}")], budget=b2)
    with pytest.raises(BudgetExceeded):
        c.complete(task="verifier", system="s", prompt="p")
    assert t.calls == []


def test_budget_accumulates_then_stops():
    b = Budget(max_usd=0.05)
    c, t, _ = client([resp("{}", 1000, 1000)] * 5, budget=b,
                     config=LLMConfig(max_output_tokens=500))
    n = 0
    with pytest.raises(BudgetExceeded):
        for i in range(5):
            c.complete(task="verifier", system="s", prompt=f"p{i}")
            n += 1
    assert 0 < n < 5 and len(t.calls) == n


def test_cache_hit_avoids_transport(tmp_path):
    c, t, _ = client([resp('{"x":1}')], cache_dir=tmp_path / "cache")
    r1 = c.complete(task="cross_ref", system="s", prompt="p")
    r2 = c.complete(task="cross_ref", system="s", prompt="p")
    assert len(t.calls) == 1 and r2.cached and r2.text == r1.text and r2.cost_usd == 0
    # different prompt_version misses
    c.transport = None
    t.seq.append(resp('{"x":2}'))
    r3 = c.complete(task="cross_ref", system="s", prompt="p", prompt_version="v2")
    assert not r3.cached and len(t.calls) == 2


def test_audit_entries(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    c, t, _ = client([resp('{"x":1}', 10, 5)], audit=log, cache_dir=tmp_path / "c", agent="extractor")
    c.complete(task="chunk_extract", system="s", prompt="p", prompt_version="v7")
    c.complete(task="chunk_extract", system="s", prompt="p", prompt_version="v7")
    es = read_audit(tmp_path / "audit.jsonl")
    assert len(es) == 2
    e = es[0]
    assert (e["agent"], e["action"], e["model"]) == ("extractor", "llm_call", "claude-haiku-5-5")
    assert e["tokens_in"] == 10 and e["tokens_out"] == 5 and e["prompt_version"] == "v7"
    assert len(e["input_hash"]) == 64 and e["cost_usd"] > 0
    assert es[1]["cached"] is True and es[1]["input_hash"] == e["input_hash"]


def test_config_load_and_estimate(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"llm": {"models": {"chunk_extract": "gpt-4o-mini"},
                                     "pricing": {"gpt-4o-mini": [0.15, 0.6]}}}))
    cfg = LLMConfig.load(p)
    assert cfg.model_for("chunk_extract") == "gpt-4o-mini"
    est = estimate_cost([("chunk_extract", 1_000_000, 1_000_000)], cfg)
    assert est == pytest.approx(0.75)
    d = estimate_cost([("chunk_extract", 1_000_000, 0), ("verifier", 0, 1_000_000)], LLMConfig())
    assert d == pytest.approx(1.0 + 15.0)
