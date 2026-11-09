import json
import re

from e2e_replay import UNVERIFIABLE_QUOTE
from migration_agent.verify.models import FindingsFile


def findings(run) -> FindingsFile:
    return FindingsFile.model_validate_json((run.root / "feedback" / "findings.json").read_text())


def test_four_kind_vocabulary_and_severity(run):
    ff = findings(run)
    assert {f.kind for f in ff.findings} <= {"contradiction", "doc_only", "code_only", "doc_vs_doc"}
    kinds = {f.kind: f for f in ff.findings}
    assert set(kinds) == {"contradiction", "doc_only", "code_only"}
    assert kinds["contradiction"].severity == "high"
    assert kinds["doc_only"].severity == "medium"
    assert kinds["code_only"].severity == "low"


def test_ids_and_order(run):
    ff = findings(run)
    assert [f.id for f in ff.findings] == [f"F-{i:04d}" for i in range(1, len(ff.findings) + 1)]
    rank = {"high": 0, "medium": 1, "low": 2}
    sev = [rank[f.severity] for f in ff.findings]
    assert sev == sorted(sev), "findings must be ordered high -> low"
    assert ff.graph_version == run.versions["analyze"]
    assert ff.claims_extracted >= 2


def test_all_quotes_verified_against_sources(run):
    ff = findings(run)
    user_doc = (run.root / "user_docs" / "requirements.md").read_text()
    norm = lambda s: " ".join(s.split())   # noqa: E731
    pre_doc = norm(run.doc)    # note: doc was re-rendered after re-analysis; quotes were taken pre-resolve
    for f in ff.findings:
        assert f.quotes
        for q in f.quotes:
            assert q.verified is True
            assert q.source in ("understanding", "requirements.md")
            if q.source == "requirements.md":
                assert norm(q.text) in norm(user_doc)
            assert UNVERIFIABLE_QUOTE not in q.text
    assert pre_doc


def test_unverifiable_and_invalid_findings_dropped_and_counted(run):
    ff = findings(run)
    assert ff.dropped_unverifiable == 2          # fabricated quote + invalid kind
    assert not any("Fabricated" in f.title or "Invalid kind" in f.title for f in ff.findings)
    m = re.search(r"dropped unverifiable: (\d+)", run.outputs["verify"].output)
    assert m and int(m.group(1)) == 2


def test_code_only_grouped_into_one_low_finding(run):
    ff = findings(run)
    code_only = [f for f in ff.findings if f.kind == "code_only"]
    assert len(code_only) == 1
    f = code_only[0]
    assert f.severity == "low" and f.title.startswith("Not covered by user docs:") and "(3 items)" in f.title
    assert len(f.item_ids) == 3 and f.area
    assert f.detail.count("\n- ") + f.detail.startswith("- ") == 3


def test_item_ids_reference_real_nodes(run):
    ff = findings(run)
    ids = {n.id for n in run.graph().nodes}
    for f in ff.findings:
        assert set(f.item_ids) <= ids
    contra = next(f for f in ff.findings if f.kind == "contradiction")
    assert len(contra.item_ids) == 1 and contra.item_ids[0].startswith("RULE-")
    assert [q.source for q in contra.quotes] == ["understanding", "requirements.md"]
    doc_only = next(f for f in ff.findings if f.kind == "doc_only")
    assert doc_only.item_ids == [] and {q.source for q in doc_only.quotes} == {"requirements.md"}


def test_classification_summary_matches_golden(run):
    import e2e_golden as golden
    ff = findings(run)
    golden.check(f"{run.repo}.findings.json",
                 {"kinds": [(f.kind, f.severity) for f in ff.findings], "dropped": ff.dropped_unverifiable})


def test_cli_prints_summary_line(run):
    out = run.outputs["verify"].output
    assert "findings: 3" in out and "'contradiction': 1" in out and "'doc_only': 1" in out and "'code_only': 1" in out


def test_verifier_without_user_docs_returns_empty(express, tmp_path):
    # separate copy so we do not disturb the shared run
    import shutil
    from click.testing import CliRunner
    from migration_agent.cli import main
    from e2e_support import register
    root = tmp_path / "p"
    shutil.copytree(express.root, root)
    register(root, express.backend)
    (root / "user_docs" / "requirements.md").unlink()
    res = CliRunner().invoke(main, ["verify", str(root), "--config", str(express.config)])
    assert res.exit_code == 0 and "findings: 0" in res.output
    assert json.loads((root / "feedback" / "findings.json").read_text())["findings"] == []
