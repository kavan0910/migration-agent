"""Score a project's latest graph against eval/repos/<repo>/expected.json.
usage: PYTHONPATH=src python scripts/score.py <project_dir> <eval_repo>"""
import sys
from pathlib import Path

from migration_agent.evalharness import load_actual, load_expected, score

proj, repo = Path(sys.argv[1]), sys.argv[2]
r = score(load_actual(proj / "graph"), load_expected(repo), Path(__file__).resolve().parents[1] / "eval" / "repos" / repo)
for k, c in r.categories.items():
    print(f"{k:10} P={c.precision:.2f} R={c.recall:.2f} F1={c.f1:.2f} exp={c.expected} act={c.actual}")
    if "-v" in sys.argv:
        print("   missed:", c.missed, "\n   spurious:", c.spurious)
print("evidence validity", r.evidence_validity, r.evidence_errors[:3], "| injection hits", r.injection_hits)
