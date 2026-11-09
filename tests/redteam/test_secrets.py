"""D-06(d): a secret-laden repo must never leak into prompts, traces, caches, checkpoints, graphs,
docs or review-UI state; redaction placeholders list the finding without the value."""
from __future__ import annotations

import http.client
import json

import pytest

from migration_agent.ingest.secrets import redact_text, scan_text
from migration_agent.review import comments as rcomments
from migration_agent.review import resolutions as rresolutions
from migration_agent.review.reanalyze import reanalyze
from migration_agent.review_ui.server import serve
from migration_agent.verify.verifier import run_verifier

from rt_helpers import RecordingTransport, analyze, llm_client, make_project, tree_text

VALUE_KEYS = ["aws_access_key", "aws_secret", "github", "slack", "stripe", "gcp_api_key", "jwt", "db_password",
              "config_password", "private_key_body", "env_secret", "api_key_env"]


def _values(s):
    return [s[k] for k in VALUE_KEYS]


def _assert_clean(blob_by_name: dict[str, str], secrets: dict[str, str], where: str):
    for name, text in blob_by_name.items():
        for k in VALUE_KEYS:
            assert secrets[k] not in text, f"{k} leaked into {where}:{name}"


@pytest.fixture
def world(tmp_path, mk, monkeypatch):
    repo = tmp_path / "repo"
    secrets = mk.make_secret_repo(repo)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secrets["api_key_env"])
    proj, rep = make_project(tmp_path, repo)
    return proj, rep, secrets


def test_ingest_withholds_credential_files_and_redacts_the_rest(world):
    proj, rep, s = world
    code = tree_text(proj.code)
    for withheld in (".env", "id_rsa", "certs/server.pem"):
        assert withheld not in code, withheld
    _assert_clean(code, s, "code/")
    assert any("[REDACTED:" in t for t in code.values())
    assert "[REDACTED:private_key]" in code["deploy/notes.txt"]
    assert "-----END RSA PRIVATE KEY-----" not in code["deploy/notes.txt"]
    reasons = {e.path: e.reason for e in rep.exclusions}
    assert reasons[".env"] == reasons["id_rsa"] == reasons["certs/server.pem"] == "secret_file"


def test_report_lists_findings_without_values(world):
    proj, rep, s = world
    kinds = {f.kind for f in rep.secret_findings}
    assert {"aws_access_key", "github_token", "slack_token", "stripe_key", "gcp_api_key", "jwt",
            "db_url_password", "generic_secret", "private_key", "secret_file"} <= kinds
    assert {".env", "id_rsa", "src/app.js", "config/settings.yml"} <= set(rep.files_with_secrets)
    dumped = (proj.root / "ingest_report.json").read_text()
    _assert_clean({"ingest_report.json": dumped}, s, "report")
    for f in rep.secret_findings:
        if f.line:
            assert f.file and "[REDACTED:" in f.preview


def test_redaction_is_exact_and_keeps_surrounding_code(mk):
    s = mk.fake_secrets()
    src = f'const k = "{s["github"]}"; const db = "postgres://u:{s["db_password"]}@h/db"; x = 1\n'
    out, f = redact_text("a.js", src)
    assert s["github"] not in out and s["db_password"] not in out
    assert out.startswith('const k = "[REDACTED:github_token]";') and "postgres://u:[REDACTED:db_url_password]@h/db" in out
    assert out.rstrip().endswith("x = 1")
    assert len(f) == 2 and not any(s["github"] in x.preview for x in f)


def test_placeholders_and_env_references_are_not_flagged():
    assert scan_text("a.py", 'password = os.environ["PW"]\napi_key = "${API_KEY}"\ntoken = getenv("T")\n') == []
    assert scan_text("a.py", 'password = "[REDACTED:generic_secret]"\n') == []


def test_long_minified_line_secret_beyond_5000_chars_is_redacted(mk):
    s = mk.fake_secrets()
    line = "a=1;" * 3000 + f'var t="{s["slack"]}";' + "b=2;" * 100
    out, _ = redact_text("min.js", line)
    assert s["slack"] not in out


def test_nothing_secret_in_any_prompt_trace_cache_or_artifact(world, tmp_path):
    proj, rep, s = world
    # --- analyze (chunk_extract, business_rules, cross_ref, gap_fill)
    t = RecordingTransport()
    analyze(proj, t)
    assert t.requests, "no LLM traffic recorded"
    _assert_clean({"all_prompts": t.all_text}, s, "LLM requests")

    # --- verify with a user doc that itself pastes credentials and an injection attempt
    (proj.root / "user_docs").mkdir(exist_ok=True)
    (proj.root / "user_docs" / "spec.md").write_text(
        f"# Spec\n\nThe service uses token {s['github']} and DB postgres://app:{s['db_password']}@db/app.\n\n"
        "## Health\n\nGET /health returns ok.\n")

    def reply(system, user):
        if "extract factual claims" in system:
            doc = user.split("<<<DOC_BEGIN>>>", 1)[1].split("<<<DOC_END>>>", 1)[0]
            q = next((ln for ln in doc.splitlines() if "GET /health" in ln), "")
            return json.dumps({"claims": [{"text": "health", "quote": q}] if q else []})
        if "compare claims" in system.lower() or "CLAIMS_BEGIN" in user:
            claims = json.loads(user.split("<<<CLAIMS_BEGIN>>>", 1)[1].split("<<<CLAIMS_END>>>", 1)[0])
            c = next((c for c in claims if c["source"] != "understanding"), None)
            if not c:
                return json.dumps({"findings": []})
            return json.dumps({"findings": [{"kind": "doc_only", "severity": "low", "title": "health",
                                             "detail": "d", "item_ids": ["COMP-0001"],
                                             "quotes": [{"source": c["source"], "text": c["quote"]}]}]})
        return json.dumps({"nodes": [], "edges": [], "unresolved": []})

    vt = RecordingTransport(reply=reply)
    res = run_verifier(proj, llm_client(proj, vt))
    _assert_clean({"verifier_requests": vt.all_text}, s, "verifier prompts")
    assert res.findings, "fixture should produce one verified finding"

    # --- review: comment + resolution carrying pasted credentials, then targeted re-analysis
    from migration_agent.graph import load_latest
    node_id = load_latest(proj.graph_dir).nodes[0].id
    rcomments.add_comment(proj, node_id, f"is this right? key={s['github']} and {s['aws_access_key']}")
    rresolutions.add_resolution(proj, res.findings[0].id, "needs_reanalysis", f"see postgres://a:{s['db_password']}@h/x")
    rt = RecordingTransport()
    reanalyze(proj, llm_client(proj, rt))
    assert rt.requests, "reanalysis should have called the model"
    _assert_clean({"reanalyze_requests": rt.all_text}, s, "reanalysis prompts")

    # --- every file the project produced (audit, llm_cache, checkpoint, graph snapshots, docs, facts, feedback)
    produced = tree_text(proj.root, skip=("user_docs",))
    assert any(k.startswith("llm_cache/") for k in produced)
    assert any(k.startswith("checkpoint/") for k in produced)
    assert any(k.startswith("graph/snapshots/") for k in produced)
    assert "audit/audit.jsonl" in produced and "docs/understanding.latest.md" in produced
    _assert_clean(produced, s, "project artifact")
    assert "[REDACTED:" in produced["feedback/comments.json"]


def test_audit_scrubs_credentials_in_free_text(tmp_path, mk):
    from migration_agent.audit import AuditLog, read_audit
    s = mk.fake_secrets()
    log = AuditLog(tmp_path / "a.jsonl")
    log.record("x", "act", command=f"run --token {s['github']}", error=f"password={s['config_password']} failed",
               nested={"u": f"postgres://a:{s['db_password']}@h/d"})
    with pytest.raises(RuntimeError):
        with log.span("x", "boom"):
            raise RuntimeError(f"bad key {s['aws_access_key']}")
    text = (tmp_path / "a.jsonl").read_text()
    for k in ("github", "config_password", "db_password", "aws_access_key"):
        assert s[k] not in text
    assert len(read_audit(tmp_path / "a.jsonl")) == 2


def test_review_server_state_never_contains_secrets(world):
    proj, rep, s = world
    analyze(proj, RecordingTransport())
    from migration_agent.graph import load_latest
    node_id = load_latest(proj.graph_dir).nodes[0].id
    server, url = serve(proj.root)
    try:
        host, port = "127.0.0.1", server.port
        base = "/" + url.rsplit("/", 2)[-2]
        c = http.client.HTTPConnection(host, port)
        body = json.dumps({"item_id": node_id, "text": f"leak {s['stripe']} {s['jwt']}", "author": s["github"]})
        c.request("POST", base + "/api/comments", body, {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
        r = c.getresponse()
        assert r.status == 201
        posted = r.read().decode()
        c.request("GET", base + "/api/state", headers={"Host": f"127.0.0.1:{port}"})
        state = c.getresponse().read().decode()
        c.request("GET", base + "/graph", headers={"Host": f"127.0.0.1:{port}"})
        graph_page = c.getresponse().read().decode()
    finally:
        server.stop()
    _assert_clean({"post": posted, "state": state, "graph": graph_page}, s, "review UI")
    assert "[REDACTED:" in state
