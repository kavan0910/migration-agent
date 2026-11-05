"""Run the test suite plus end-to-end checks and write a Lavish HTML report.

Usage: PYTHONPATH=src .venv/bin/python scripts/test_report.py [out.html]
"""
from __future__ import annotations

import html
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEME = Path.home() / "Documents/Code/.claude/skills/lavish/memorable-theme.css"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Downloads/Code/.lavish/migration-agent-phase-a-test-report.html"
PY = str(ROOT / ".venv/bin/python")
ENV = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin:/usr/local/bin"}

# test file -> (task, what it proves, how it is tested)
WHAT = {
    "test_ingest_engine": ("A-02 Ingestion", "Archives and repos are copied safely; limits and secret scan work.",
        "Builds malicious zips/tars in temp dirs (../ paths, escaping symlinks, zip bombs, oversize), a real local git repo cloned over file://, and a plain directory. Asserts errors, exclusions, redacted secret previews and that secret values never appear in the report."),
    "test_treesitter": ("A-03 Tree-sitter", "Classes, functions, imports are extracted for Python, TS/JS, Java, Go, C#; unknown languages fall back.",
        "Small fixture file per language; asserts names, line ranges, params, decorators; a COBOL file proves the fallback path; a broken file proves partial parsing; checks determinism and JSON round-trip."),
    "test_manifests": ("A-04 Manifests", "pom.xml, package.json, go.mod, requirements, Cargo, pyproject, gradle, csproj are parsed.",
        "Fixture manifest per ecosystem incl. a monorepo and malformed files; asserts dependencies, runtime versions and that bad files set parse_error instead of crashing."),
    "test_config": ("A-05 Config", "Config keys found in env, YAML, properties, JSON, TOML, ini, compose; secrets never stored.",
        "Fixture config files with fake secrets; asserts keys/lines and that none of the fixture secret strings appear anywhere in the serialized output."),
    "test_audit": ("A-07 Audit log", "Append-only JSONL with monotonic sequence numbers and totals.",
        "Writes entries, spans (incl. exceptions) and 4 concurrent processes appending at once; asserts no lost or interleaved lines and correct totals."),
    "test_graph_models": ("A-08 Graph models", "Nodes/edges validate; confidence is derived from evidence; bad IDs and dangling edges are rejected.",
        "Constructs valid and invalid models and asserts pydantic errors."),
    "test_graph_registry_store": ("A-08 Registry & store", "Stable deterministic IDs, snapshots with a pointer file, schema generation and graph diff.",
        "Allocates IDs in shuffled order and across fresh processes; saves snapshots, loads latest, corrupts files to check errors, diffs graphs."),
    "test_sandbox_tools": ("A-09 Sandbox tools", "Path-jailed read-only tools reject traversal and symlink escapes; no write/exec surface.",
        "Tries ../, absolute paths, NUL bytes and escaping symlinks; asserts caps on size/lines and that the class has no mutating methods."),
    "test_sandbox_guard": ("A-09 Sandbox guard", "In-process guard blocks writes, process spawn and non-allowlisted network.",
        "Each case runs in a subprocess so the audit hook cannot leak; attempts a write, remove, Popen, os.system, TCP/UDP/DNS to a blocked host; allowlisted localhost connect must succeed."),
    "test_eval_harness": ("A-11 Evaluation harness", "Scorer gives precision/recall, enforces evidence and injection gates, detects regressions.",
        "Feeds the expected.json itself (perfect), partial, spurious, injected and invalid-evidence graphs; verifies every cited file:line in the 3 reference repos really exists."),
    "test_walking_skeleton": ("A-01/A-06/A-10 CLI pipeline", "init → ingest → extract → render → audit works end to end with stable IDs.",
        "Invokes the real click CLI against the express-crud repo and asserts the rendered doc, full coverage mapping and that the source repo is untouched."),
}

NOT_TESTED = [
    ("Docker image / run.sh / compose / Squid proxy", "Docker is not installed on this machine. Files were only syntax-checked (bash -n). The in-process guard and path-jail ARE tested."),
    ("LLM-based analysis", "Phase B. The graph here is facts-only, so precision/recall against expected.json is not scored yet."),
    ("Large repos (2000+ files)", "Phase D. Reference repos are scaled down: spring-lite 11 files, django-lite 8 (plan says ~100 and ~500)."),
    ("Real secret exposure on GitHub", "Fixtures use fake keys only."),
]


def sh(*args: str) -> tuple[int, str]:
    p = subprocess.run(args, cwd=ROOT, env=ENV, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def run_pytest():
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "j.xml"
        rc, out = sh(PY, "-m", "pytest", "-q", f"--junitxml={xml}")
        suite = ET.parse(xml).getroot()
        suite = suite if suite.tag == "testsuite" else suite[0]
        cases = []
        for tc in suite.iter("testcase"):
            status = "pass"
            for c in tc:
                if c.tag in ("failure", "error"):
                    status = "fail"
                elif c.tag == "skipped":
                    status = "skip"
            cases.append((tc.get("classname").split(".")[-1], tc.get("name"), status, float(tc.get("time") or 0)))
        return rc, out.splitlines()[-1] if out else "", cases


def e2e():
    rows = []
    tmp = Path(tempfile.mkdtemp())
    srcs = []
    for r in ("express-crud", "spring-lite", "django-lite"):
        srcs.append((f"dir: {r}", str(ROOT / "eval/repos" / r)))
    base = ROOT / "eval/repos/express-crud"
    z = tmp / "express.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for f in base.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(base.parent))
    t = tmp / "express.tgz"
    with tarfile.open(t, "w:gz") as tf:
        tf.add(base, arcname="express-crud")
    srcs += [("zip: express-crud", str(z)), ("tar.gz: express-crud", str(t))]
    for label, src in srcs:
        proj = tmp / (label.replace(": ", "_").replace(".", "") + ".migration")
        outs, ok = [], True
        for cmd in (["init", src, "--project", str(proj)], ["ingest", str(proj)], ["extract", str(proj)], ["render", str(proj)]):
            rc, o = sh(PY, "-m", "migration_agent.cli", *cmd)
            ok &= rc == 0
            if cmd[0] in ("ingest", "extract"):
                outs.append(o.splitlines()[-1] if o else "")
        rows.append((label, ok, outs))
    return rows


def esc(s):
    return html.escape(str(s))


def main():
    rc, summary, cases = run_pytest()
    e2e_rows = e2e()
    by = {}
    for mod, name, st, t in cases:
        by.setdefault(mod, []).append((name, st, t))
    total = len(cases)
    passed = sum(1 for c in cases if c[2] == "pass")
    failed = sum(1 for c in cases if c[2] == "fail")
    css = THEME.read_text() if THEME.exists() else ""
    ok_all = rc == 0 and failed == 0 and all(r[1] for r in e2e_rows)

    mods = ""
    for mod, items in sorted(by.items(), key=lambda kv: WHAT.get(kv[0], ("~",))[0]):
        task, what, how = WHAT.get(mod, (mod, "", ""))
        p = sum(1 for i in items if i[1] == "pass")
        badge = "ok" if p == len(items) else "bad"
        lis = "".join(f'<li class="{"" if s=="pass" else "bad"}">{"✓" if s=="pass" else "✗"} <code>{esc(n)}</code></li>' for n, s, _ in items)
        mods += f'''<section class="card"><span class="label">{esc(task)}</span>
<h3>{esc(what)} <span class="pill {badge}">{p}/{len(items)} passed</span></h3>
<p><b>How it was tested.</b> {esc(how)}</p>
<details><summary>{len(items)} test cases · <code>tests/{esc(mod)}.py</code></summary><ul class="tl">{lis}</ul></details></section>'''

    e2e_html = "".join(
        f'<tr><td>{esc(l)}</td><td class="{"okc" if ok else "badc"}">{"pass" if ok else "FAIL"}</td><td><code>{esc(" · ".join(o))}</code></td></tr>'
        for l, ok, o in e2e_rows)
    nt = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in NOT_TESTED)

    doc = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Phase A Test Report</title>
<style>{css}
.pill{{font:500 11px/1 var(--mono,monospace);padding:3px 8px;border-radius:999px;border:1px solid var(--line-strong,#0003);vertical-align:middle;margin-left:8px}}
.pill.ok,.okc{{color:var(--ok,#047857)}} .pill.bad,.badc,li.bad{{color:var(--err,#c92a2a)}}
.tl{{columns:2;list-style:none;padding:0;margin:8px 0 0;font-size:12px}} .tl li{{margin:2px 0;break-inside:avoid}}
.stats{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}} .stat{{flex:1;min-width:140px}} .stat b{{display:block;font-size:30px}}
@media(max-width:640px){{.tl{{columns:1}}}}
</style>
<script>try{{var t=localStorage.getItem('lavish-theme');if(t)document.documentElement.dataset.theme=t}}catch(e){{}}</script>
</head><body>
<button class="theme-toggle" type="button" onclick="(function(){{var r=document.documentElement,d=r.dataset.theme==='dark'?'light':'dark';r.dataset.theme=d;try{{localStorage.setItem('lavish-theme',d)}}catch(e){{}}}})()">Light / Dark</button>
<main>
<span class="label">PHASE A · TEST REPORT</span>
<h1>{"All checks passed" if ok_all else "Failures found"}</h1>
<p>Generated {datetime.now():%Y-%m-%d %H:%M} by <code>scripts/test_report.py</code> · pytest summary: <code>{esc(summary)}</code></p>
<div class="stats">
<div class="card stat"><span class="label">UNIT TESTS</span><b>{passed}/{total}</b>pass</div>
<div class="card stat"><span class="label">FAILED</span><b class="{"badc" if failed else "okc"}">{failed}</b></div>
<div class="card stat"><span class="label">END-TO-END RUNS</span><b>{sum(1 for r in e2e_rows if r[1])}/{len(e2e_rows)}</b>pass</div>
<div class="card stat"><span class="label">MODULES</span><b>{len(by)}</b></div>
</div>
<h2>How testing works</h2>
<div class="card"><p>Two layers. <b>1. Automated unit/integration tests</b> run with <code>pytest</code> against fixtures built in temp directories, so hostile inputs (zip bombs, traversal, secrets) are generated on the fly and nothing real is touched. Sandbox guard tests run in subprocesses so the audit hook cannot leak. <b>2. End-to-end runs</b> drive the real CLI (<code>init → ingest → extract → render</code>) on the three reference repos and on zip and tar.gz copies, asserting each step exits 0.</p></div>
<h2>Results by component</h2>
{mods}
<h2>End-to-end CLI runs</h2>
<div class="card"><table><tr><th>Source</th><th>Result</th><th>ingest / extract output</th></tr>{e2e_html}</table></div>
<h2>Not tested yet</h2>
<div class="card"><table><tr><th>Area</th><th>Why / status</th></tr>{nt}</table></div>
</main></body></html>'''
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc)
    print(OUT, "OK" if ok_all else "FAILURES")


main()
