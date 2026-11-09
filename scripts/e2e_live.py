"""Optional live end-to-end run against a real model (NOT run in CI, never required by pytest).

usage: PYTHONPATH=src python scripts/e2e_live.py <llm-config.json> [repo ...] [--max-usd N] [--keep DIR]

Reads API keys from .env (via the CLI). For each eval repo: init -> ingest -> extract -> analyze, then prints
evalharness precision/recall/F1 per category, evidence validity and the audit cost totals. Exit code 1 if an
evidence/injection gate fails.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from click.testing import CliRunner

from migration_agent.cli import main
from migration_agent.evalharness import EVAL_REPOS, load_actual, load_expected, score

DEFAULT_REPOS = ["express-crud", "spring-lite", "django-lite"]


def run_repo(repo: str, config: Path, work: Path, max_usd: float | None) -> bool:
    root = work / f"{repo}.migration"
    steps = [["init", str(EVAL_REPOS / repo), "--project", str(root)], ["ingest", str(root)], ["extract", str(root)],
             ["analyze", str(root), "--config", str(config)] + (["--max-usd", str(max_usd)] if max_usd else [])]
    for args in steps:
        res = CliRunner().invoke(main, args)
        print(f"[{repo}] {args[0]}: exit {res.exit_code}")
        if res.exit_code:
            print(res.output)
            return False
    rep = score(load_actual(root / "graph"), load_expected(repo))
    print(f"\n== {repo} ==")
    for cat, c in rep.categories.items():
        print(f"  {cat:11} P={c.precision:.2f} R={c.recall:.2f} F1={c.f1:.2f} (exp {c.expected}, act {c.actual})")
    print(f"  evidence validity {rep.evidence_validity:.2%}; injection hits {len(rep.injection_hits)}; gates {rep.gates}")
    audit = json.loads(CliRunner().invoke(main, ["audit", str(root), "--format", "json"]).output)["totals"]
    print(f"  audit: {audit}")
    return rep.passed


def main_cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config", type=Path)
    ap.add_argument("repos", nargs="*", default=DEFAULT_REPOS)
    ap.add_argument("--max-usd", type=float, default=None)
    ap.add_argument("--keep", type=Path, default=None, help="keep projects in this directory")
    a = ap.parse_args()
    work = a.keep or Path(tempfile.mkdtemp(prefix="e2e-live-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"projects in {work}")
    ok = all([run_repo(r, a.config, work, a.max_usd) for r in a.repos])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_cli())
