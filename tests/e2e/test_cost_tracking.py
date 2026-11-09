import json

import pytest
from click.testing import CliRunner

from e2e_support import invoke, make_run
from e2e_replay import write_config
from migration_agent.analyze.runner import estimate
from migration_agent.cli import main
from migration_agent.llm.litellm_client import LLMConfig

PRICE = (3.0, 15.0)


def _cost(call):
    return (call["tokens_in"] * PRICE[0] + call["tokens_out"] * PRICE[1]) / 1_000_000


def test_audit_totals_equal_sum_of_per_call_costs(run):
    calls = run.llm_calls()
    assert calls and all(c["cost_usd"] > 0 for c in calls if not c["cached"])
    out = CliRunner().invoke(main, ["audit", str(run.root), "--format", "json"])
    assert out.exit_code == 0
    totals = json.loads(out.output)["totals"]
    assert totals["cost_usd"] == pytest.approx(sum(c["cost_usd"] for c in calls), abs=1e-6)
    assert totals["tokens_in"] == sum(c["tokens_in"] for c in calls)
    assert totals["tokens_out"] == sum(c["tokens_out"] for c in calls)
    assert totals["errors"] == 0
    assert "totals:" in CliRunner().invoke(main, ["audit", str(run.root)]).output


def test_audit_matches_independent_replay_accounting(run):
    """Per-call cost recomputed from the transport's own token counts and the configured prices."""
    calls = run.llm_calls()
    assert len(calls) == len(run.backend.calls) - (run.rerun_calls_after - run.rerun_calls_before) \
        - (run.rerun2_calls_after - run.rerun2_calls_before)
    main_calls = [c for c in run.backend.calls][:len(calls)]
    assert [c["task"] for c in main_calls][:2] == ["chunk_extract", "business_rules"]
    for audited, replayed in zip(calls, main_calls):
        assert audited["task"] == replayed["task"]
        assert audited["tokens_in"] == replayed["tokens_in"] and audited["tokens_out"] == replayed["tokens_out"]
        assert audited["cost_usd"] == pytest.approx(_cost(replayed), abs=1e-9)


def test_totals_are_cumulative_across_analyze_verify_resolve(run):
    calls = run.llm_calls()
    by_task: dict[str, float] = {}
    for c in calls:
        by_task[c["task"]] = by_task.get(c["task"], 0) + c["cost_usd"]
    # analyze: chunk_extract+business_rules; verify: 3 verifier calls; resolve x2: 2 re-analysis chunk_extract
    assert {"chunk_extract", "business_rules", "verifier"} <= set(by_task)
    n = lambda t: sum(1 for c in calls if c["task"] == t)   # noqa: E731
    assert n("verifier") == 3
    assert n("chunk_extract") == len(run.analyze_calls) // 2 + 2
    analyze_cost = sum(_cost(c) for c in run.analyze_calls)
    assert sum(c["cost_usd"] for c in calls) > analyze_cost > 0
    # the cost is monotone in the log (cumulative sums only grow)
    run_sum = 0.0
    for c in calls:
        assert c["cost_usd"] >= 0
        run_sum += c["cost_usd"]
    assert run_sum == pytest.approx(sum(by_task.values()))


def test_cache_hits_cost_zero(run):
    appended = run.llm_calls(run.rerun2_root)[len(run.llm_calls(run.rerun_root)):]
    assert len(appended) == len(run.analyze_calls), "pure re-run should replay exactly the analyze calls"
    assert all(c["cached"] and c["cost_usd"] == 0 for c in appended)
    assert all(c["tokens_in"] > 0 for c in appended), "tokens are still reported for cached calls"


def test_cached_calls_do_not_inflate_totals(run):
    out = json.loads(CliRunner().invoke(main, ["audit", str(run.rerun2_root), "--format", "json"]).output)
    paid = sum(e["cost_usd"] for e in out["entries"] if e["action"] == "llm_call" and not e["cached"])
    assert out["totals"]["cost_usd"] == pytest.approx(paid, abs=1e-6)
    assert all(e["cost_usd"] == 0 for e in out["entries"] if e.get("cached"))


def test_dry_run_estimate_within_2x_of_replay_actual(run, tmp_path, mp):
    fresh = make_run(run.repo, tmp_path, mp, tag="est")          # extracted, nothing analysed yet
    cfg = LLMConfig.load(fresh.config)
    n, est = estimate(fresh.proj, cfg)
    actual = sum(_cost(c) for c in run.analyze_calls)
    assert n >= 1 and actual > 0
    assert 0.5 <= est / actual <= 2.0, (est, actual)
    res = CliRunner().invoke(main, ["analyze", str(fresh.root), "--config", str(fresh.config), "--dry-run"])
    assert res.exit_code == 0 and f"estimated cost ${est:.2f}" in res.output and f"{n} chunk(s)" in res.output
    assert fresh.backend.calls == [] and not (fresh.root / "docs" / "understanding.latest.md").exists()


# ---- budget guard ---------------------------------------------------------------------------------------
@pytest.fixture()
def budget_run(tmp_path, mp):
    # free input, 1000 USD/M output: the pre-call estimate is exactly 4096*1000/1e6 = 4.096 USD
    r = make_run("express-crud", tmp_path, mp, tag="budget")
    write_config(r.config, price_in=0.0, price_out=1000.0)
    r.backend.price = (0.0, 1000.0)
    return r


def test_max_usd_stops_gracefully_with_partial_results(budget_run):
    r = budget_run
    est_one_call = 4096 * 1000.0 / 1e6
    res = invoke(r, "analyze", ["analyze", str(r.root), "--config", str(r.config), "--max-usd", str(est_one_call + 1e-9)])
    assert "budget" in res.output.lower() and "'stopped': None" not in res.output
    g = r.graph()                                    # partial results are still produced and valid
    assert g is not None and g.nodes
    assert (r.root / "docs" / "understanding.latest.md").exists() and (r.root / "docs" / "graph.html").exists()
    n_paid = len(r.llm_calls())
    assert n_paid == len(r.backend.calls) and n_paid >= 1
    assert n_paid < 2, "the guard must stop before all (chunk_extract + business_rules) calls are made"


def test_resume_after_budget_stop_reuses_paid_calls(budget_run):
    r = budget_run
    est_one_call = 4096 * 1000.0 / 1e6
    invoke(r, "stopped", ["analyze", str(r.root), "--config", str(r.config), "--max-usd", str(est_one_call + 1e-9)])
    partial_nodes = len(r.graph().nodes)
    paid_before = len(r.backend.calls)
    invoke(r, "resumed", ["analyze", str(r.root), "--config", str(r.config)])
    assert "'stopped': None" in r.outputs["resumed"].output
    assert len(r.graph().nodes) > partial_nodes
    tasks = [c["task"] for c in r.backend.calls]
    assert sorted(tasks) == ["business_rules", "chunk_extract"], "each task paid for exactly once across both runs"
    assert len(r.backend.calls) == paid_before + 1


def test_no_key_and_no_offline_fails_cleanly(tmp_path, mp, monkeypatch):
    r = make_run("express-crud", tmp_path, mp, tag="nokey")
    monkeypatch.setattr("migration_agent.dotenv.load_dotenv", lambda *a, **k: None)   # a developer .env must not leak in
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    res = CliRunner().invoke(main, ["analyze", str(r.root), "--config", str(r.config)])
    assert res.exit_code != 0 and "API key" in res.output
    assert r.backend.calls == []
