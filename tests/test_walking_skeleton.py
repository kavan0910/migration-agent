import shutil
from pathlib import Path

from click.testing import CliRunner

from migration_agent.cli import main

REPO = Path(__file__).parent.parent / "eval" / "repos" / "express-crud"


def test_init_ingest_extract_render(tmp_path):
    proj = tmp_path / "p.migration"
    r = CliRunner()
    for args in (["init", str(REPO), "--project", str(proj)], ["ingest", str(proj)],
                 ["extract", str(proj)], ["render", str(proj)], ["audit", str(proj)]):
        res = r.invoke(main, args)
        assert res.exit_code == 0, (args, res.output)
    doc = (proj / "docs" / "understanding.latest.md").read_text()
    assert "COMP-0001" in doc and "Mapped 29 of 29" in doc
    # the source was copied, never modified
    assert not (REPO / "code").exists()
    # stable IDs: a second render reuses them
    r.invoke(main, ["render", str(proj)])
    assert "COMP-0001" in (proj / "docs" / "understanding.latest.md").read_text()
