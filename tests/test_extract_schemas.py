import shutil
from pathlib import Path

from click.testing import CliRunner

from migration_agent.analyze.coverage import check_coverage
from migration_agent.cli import main
from migration_agent.extract.schemas import extract_schemas
from migration_agent.graph import load_latest
from migration_agent.pipeline import Inventory

FX = Path(__file__).parent / "fixtures" / "frameworks"


def facts(rel):
    return extract_schemas(rel, (FX / rel).read_text())


def cols(f):
    return [(c["name"], c["type"]) for c in f.attrs["columns"]]


def test_flyway_create_and_alter():
    c, o = facts("flyway/db/migration/V1__init.sql")
    assert (c.name, c.line, o.name, o.line) == ("customers", 2, "public.orders", 8)
    assert cols(c) == [("id", "BIGINT"), ("email", "VARCHAR(255)"), ("balance", "NUMERIC(10, 2)")]
    assert cols(o) == [("id", "BIGINT"), ("customer_id", "BIGINT")]
    (a,) = facts("flyway/db/migration/V2__add_cols.sql")
    assert a.attrs["op"] == "alter" and cols(a) == [("phone", "VARCHAR(32)"), ("vip", "BOOLEAN")]


def test_alembic():
    c, a = facts("alembic/versions/0001_init.py")
    assert (c.name, c.line, cols(c)) == ("accounts", 7, [("id", "sa.Integer()"), ("name", "sa.String(length=50)")])
    assert a.attrs["op"] == "alter" and cols(a) == [("created_at", "sa.DateTime()")]
    assert extract_schemas("migrations/versions/x.py", "def (:") == []


def test_prisma():
    u, p = facts("prisma/schema.prisma")
    assert (u.name, u.line, p.name) == ("User", 5, "Post")
    assert cols(u) == [("id", "Int"), ("email", "String"), ("name", "String?"), ("posts", "Post[]")]


def test_typeorm():
    u, t = facts("typeorm/user.entity.ts")
    assert (u.name, u.attrs["table"]) == ("User", "users")
    assert cols(u) == [("id", "number"), ("name", "string"), ("bio", "string")]
    assert t.name == "Tag" and cols(t) == [("label", "string")]


def test_not_applicable_files_ignored():
    assert extract_schemas("src/app.py", "op.create_table('x')") == []
    assert extract_schemas("db/other.sql", "CREATE TABLE t (a int);") == []


def test_pipeline_makes_data_entity_nodes_and_coverage(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FX / "flyway", src / "flyway")
    shutil.copytree(FX / "prisma", src / "prisma")
    proj = tmp_path / "p.migration"
    r = CliRunner()
    for args in (["init", str(src), "--project", str(proj)], ["ingest", str(proj)], ["extract", str(proj)], ["render", str(proj)]):
        res = r.invoke(main, args)
        assert res.exit_code == 0, (args, res.output)
    inv = Inventory.model_validate_json((proj / "inventory.json").read_text()) if (proj / "inventory.json").exists() else None
    from migration_agent.project import Project
    p = Project(proj) if inv is None else None
    inv = inv or Inventory.model_validate_json(p.inventory_path.read_text())
    assert inv.counts["schema_entity"] == 5
    g = load_latest(Project(proj).graph_dir) if p is None else load_latest(p.graph_dir)
    ents = {(n.module, n.qualified_symbol) for n in g.nodes if n.type == "data_entity"}
    assert ("flyway/db/migration/V1__init.sql", "customers") in ents and ("prisma/schema.prisma", "User") in ents
    n = next(n for n in g.nodes if n.type == "data_entity" and n.qualified_symbol == "customers" and "V1" in n.module)
    assert n.evidence[0].line_start == 2
    assert check_coverage(inv, g).complete
