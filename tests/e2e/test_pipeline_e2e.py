import json

from migration_agent.audit import read_audit


def test_every_stage_exits_zero(run):
    for stage in ("init", "ingest", "extract", "analyze", "rerun", "verify", "resolve1", "resolve2", "changelog"):
        assert run.outputs[stage].exit_code == 0, (stage, run.outputs[stage].output)


def test_project_layout_and_artifacts(run):
    for rel in ("project.json", "inventory.json", "docs/understanding.latest.md", "docs/graph.html",
                "docs/changelog.md", "graph/latest.json", "feedback/findings.json",
                "feedback/resolutions.json", "feedback/comments.json", "audit/audit.jsonl", "facts/ast.json"):
        assert (run.root / rel).exists(), rel


def test_graph_versions_advance_through_review(run):
    v = run.versions
    assert v["analyze"] >= 1
    assert v["resolve1"] == v["analyze"] + 1
    assert v["resolve2"] == v["resolve1"] + 1
    assert "re-analyzed" in run.outputs["resolve1"].output and "re-analyzed" in run.outputs["resolve2"].output


def test_review_state_is_consistent(run):
    res = json.loads((run.root / "feedback" / "resolutions.json").read_text())["resolutions"]
    assert [r["status"] for r in res] == ["resolved"]          # consumed by re-analysis
    assert res[0]["affected_node_ids"]
    comments = json.loads((run.root / "feedback" / "comments.json").read_text())["comments"]
    assert len(comments) == 1 and comments[0]["processed"] is True and comments[0]["item_id"] == run.comment_item
    log = json.loads((run.root / "feedback" / "reanalysis_log.json").read_text())["runs"]
    assert [(r["from_version"], r["to_version"]) for r in log] == [
        (run.versions["analyze"], run.versions["resolve1"]), (run.versions["resolve1"], run.versions["resolve2"])]


def test_reanalysis_revised_the_disputed_rule(run):
    g = run.graph()
    findings = json.loads((run.root / "feedback" / "findings.json").read_text())["findings"]
    rule_id = next(f for f in findings if f["kind"] == "contradiction")["item_ids"][0]
    assert g.node(rule_id).responsibility.startswith("REVISED by reanalysis")


def test_changelog_mentions_revised_item(run):
    text = (run.root / "docs" / "changelog.md").read_text()
    findings = json.loads((run.root / "feedback" / "findings.json").read_text())["findings"]
    rule_id = next(f for f in findings if f["kind"] == "contradiction")["item_ids"][0]
    assert rule_id in text


def test_doc_refreshed_after_reanalysis(run):
    assert "REVISED by reanalysis" in run.doc


def test_audit_log_is_sequential_and_error_free(run):
    es = read_audit(run.root / "audit" / "audit.jsonl")
    assert [e["seq"] for e in es] == list(range(1, len(es) + 1))
    assert not [e for e in es if e.get("status") == "error"]
    assert {"analyze_chunks", "reanalyze"} <= {e["action"] for e in es}


def test_whole_flow_is_fast(run):
    assert run.seconds < 20
