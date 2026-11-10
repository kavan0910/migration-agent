"""End-to-end against the real engine (migration_agent.service) in offline mode: no model, no network, no spend."""
import io
import zipfile
from pathlib import Path

import pytest

service = pytest.importorskip("migration_agent.service")

from app import engine as engine_mod  # noqa: E402
from tests.conftest import drain  # noqa: E402

REPO = Path(__file__).resolve().parents[2] / "eval" / "repos" / "django-lite"
pytestmark = pytest.mark.skipif(not REPO.is_dir(), reason="eval repo not present")


def _zip_repo() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(REPO.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.name != "expected.json":
                zf.write(p, p.relative_to(REPO).as_posix())
    return buf.getvalue()


@pytest.fixture
def real(settings, db, store):
    engine_mod.set_engine(None)  # use the real module through the adapter
    settings.engine_offline = True
    from app.main import create_app
    from app.worker import Worker
    from fastapi.testclient import TestClient
    from app.db import Database
    from tests.conftest import PUBLIC_RESOLVER
    app = create_app(settings, db=db, store=store, resolver=PUBLIC_RESOLVER)
    with TestClient(app) as c:
        yield c, Worker(settings, db=Database(settings.database_url), store=store, worker_id="e2e")
    engine_mod.set_engine(None)


def test_real_engine_offline_end_to_end(real, settings):
    from tests.conftest import User
    client, worker = real
    u = User(client, "e2e@example.com")
    pid = u.project(with_source=False)
    r = u.post(f"/v1/projects/{pid}/source", files={"file": ("django-lite.zip", _zip_repo(), "application/zip")})
    assert r.status_code == 201, r.text
    assert u.post(f"/v1/projects/{pid}/user-docs", files={"file": ("spec.md", b"# Spec\nThe library lists books.\n", "text/markdown")}).status_code == 201

    est = u.post(f"/v1/projects/{pid}/estimate", json={"model_profile": "fast"})
    assert est.status_code == 200 and est.json()["chunks"] >= 1 and est.json()["allowed"] is True

    run = u.start_run(pid, model_profile="fast", max_usd=1)
    assert drain(worker) == 1
    final = u.get(f"/v1/runs/{run['id']}").json()
    assert final["status"] == "succeeded", final
    assert final["completed_stages"] == ["ingest", "extract", "analyze"]
    assert final["engine_version"]

    g = u.get(f"/v1/projects/{pid}/graph").json()
    assert g["total"] > 0 and g["version"] >= 1
    node = g["items"][0]
    assert u.get(f"/v1/projects/{pid}/graph/nodes/{node['id']}").status_code == 200
    assert u.get(f"/v1/projects/{pid}/document").text.strip()
    assert u.get(f"/v1/projects/{pid}/graph/versions").json()["total"] >= 1

    # follow-up verify run resumes from the stored project state (offline verifier)
    v = u.start_run(pid, type="verify", model_profile="fast")
    drain(worker)
    vf = u.get(f"/v1/runs/{v['id']}").json()
    assert vf["status"] in ("succeeded", "failed"), vf  # offline LLM may produce no findings; must not crash
    assert u.get(f"/v1/projects/{pid}/findings").status_code == 200
    assert not list(Path(settings.scratch_dir).glob("*"))
    # secrets of the server never leak into the run record
    assert "SECRET-KEY" not in str(final) + str(vf)
