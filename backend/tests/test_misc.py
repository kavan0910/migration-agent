import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app import billing
from app.billing import Decision, NoBilling, can_start_run, effective_limits, ensure_entitlement
from app.config import Settings
from app.db import Base, Database, normalise_url
from app.profiles import engine_config
from app.storage import LocalStore, S3Store, StoreError, make_store


def test_local_store_basics_and_traversal(tmp_path):
    s = LocalStore(tmp_path / "s")
    s.put("w/p/a/b.txt", b"1")
    s.put("w/p/c.txt", b"2")
    s.put("w/q/x.txt", b"3")
    assert s.get("w/p/a/b.txt") == b"1" and s.exists("w/p/c.txt") and not s.exists("w/p/zzz")
    assert s.list("w/p/") == ["w/p/a/b.txt", "w/p/c.txt"]
    for bad in ("../x", "/abs", "a/../../x", "a//b", "a\\b", "", "a/./b", "w/\x00"):
        with pytest.raises(StoreError):
            s.put(bad, b"x")
    with pytest.raises(StoreError):
        s.get("w/p/missing")
    with pytest.raises(StoreError):
        s.delete_prefix("w/p")  # must end with slash: "w/p" would also match "w/pq"
    assert s.delete_prefix("w/p/") == 2
    assert s.list("w/") == ["w/q/x.txt"]
    assert s.delete_prefix("w/none/") == 0
    s.put_file("w/q/y.txt", tmp_path / "s" / "w" / "q" / "x.txt")
    assert s.get("w/q/y.txt") == b"3"
    s.delete("w/q/y.txt")
    assert not s.exists("w/q/y.txt")


def test_make_store_and_s3_skeleton(settings):
    assert isinstance(make_store(settings), LocalStore)
    with pytest.raises(StoreError):
        make_store(settings.model_copy(update={"store_backend": "s3"}))

    class C:
        def __init__(self): self.objs = {}
        def put_object(self, Bucket, Key, Body, **k): self.objs[Key] = Body
        def upload_file(self, p, b, k, **kw): self.objs[k] = open(p, "rb").read()
        def get_object(self, Bucket, Key):
            import io
            return {"Body": io.BytesIO(self.objs[Key])}
        def head_object(self, Bucket, Key):
            if Key not in self.objs: raise KeyError
        def delete_object(self, Bucket, Key): self.objs.pop(Key, None)
        def list_objects_v2(self, Bucket, Prefix, **k):
            return {"Contents": [{"Key": x} for x in self.objs if x.startswith(Prefix)], "IsTruncated": False}
        def list_object_versions(self, Bucket, Prefix, **k):
            return {"Versions": [{"Key": x, "VersionId": "v1"} for x in self.objs if x.startswith(Prefix)], "IsTruncated": False}
        def delete_objects(self, Bucket, Delete, **k):
            for o in Delete["Objects"]: self.objs.pop(o["Key"], None)
            return {}

    s = S3Store("b", client=C())
    s.put("w/p/a", b"1")
    assert s.get("w/p/a") == b"1" and s.exists("w/p/a") and s.list("w/p/") == ["w/p/a"]
    assert s.delete_prefix("w/p/") == 1 and not s.exists("w/p/a")
    with pytest.raises(StoreError):
        s.get("w/p/a")
    with pytest.raises(StoreError):
        s.put("../x", b"")


def test_normalise_url():
    assert normalise_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalise_url("postgresql://u@h/d") == "postgresql+psycopg://u@h/d"
    assert normalise_url("sqlite:///x.db") == "sqlite:///x.db"


def test_billing_seam_has_no_payment_code():
    import pathlib
    root = pathlib.Path(billing.__file__).parent.parent
    banned = ("stripe", "paddle", "paypal", "braintree", "checkout", "webhook", "price_id", "secret_key")
    for py in list((root / "app").rglob("*.py")) + [root / "pyproject.toml"]:
        txt = py.read_text().lower()
        for b in banned:
            assert b not in txt, (py, b)
    n = NoBilling()
    assert n.ensure_customer("w", "n") is None and n.sync_plan("w", "p") is None and n.report_usage("w", {}) is None


def test_can_start_run_matrix(db):
    with db.session() as s:
        ws = "w1"
        from app.models import Workspace
        s.add(Workspace(id=ws, name="x"))
        s.flush()
        ensure_entitlement(s, ws, plan="internal")  # explicit assignment: the default is the restrictive free plan
        plan, lim = effective_limits(s, ws)
        assert plan == "internal" and lim["max_usd_per_run"] > 100
        assert can_start_run(s, ws, {"usd": 1.0}, 5).allowed
        ensure_entitlement(s, ws).overrides = {"max_usd_per_run": 2}
        d = can_start_run(s, ws, {"usd": 1.0}, 5)
        assert not d.allowed and d.details["limit"] == "max_usd_per_run"
        assert can_start_run(s, ws, {"usd": 1.0}, 2).allowed


def test_profiles_mapping_single_place(settings):
    for p in ("fast", "balanced", "best"):
        cfg = engine_config(p, settings)
        assert cfg["api_key"] == "sk-test-SECRET-KEY-123" and set(cfg["models"]) >= {"chunk_extract", "verifier"}
    with pytest.raises(ValueError):
        engine_config("gpt-5-whatever", settings)
    # customers cannot pass raw model ids
    assert "models" not in engine_config("fast", settings.model_copy(update={"model_api_key": None})).get("api_key", {})


def test_run_body_rejects_raw_model_ids(alice):
    pid = alice.project()
    assert alice.post(f"/v1/projects/{pid}/runs", json={"model_profile": "gpt-4o"}).status_code == 422
    assert alice.post(f"/v1/projects/{pid}/runs", json={"model": "gpt-4o"}).status_code in (202, 422)


def test_alembic_upgrade_matches_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/mig.db"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(url))
    migrated = {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names() if t != "alembic_version"}
    expected = {t.name: {c.name for c in t.columns} for t in Base.metadata.sorted_tables}
    assert migrated == expected
    command.downgrade(cfg, "base")


def test_readyz_fails_without_schema(settings, tmp_path, store):
    from fastapi.testclient import TestClient
    from app.main import create_app
    d = Database(f"sqlite:///{tmp_path}/empty.db")
    with TestClient(create_app(settings, db=d, store=store)) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/readyz").status_code == 503


def test_unhandled_error_is_generic(settings, db, store):
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app(settings, db=db, store=store)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
    assert r.status_code == 500 and "secret internal" not in r.text and r.json()["error"]["code"] == "internal_error"
