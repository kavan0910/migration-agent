from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import billing, engine as engine_mod
from app.config import Settings, set_settings
from app.db import Database
from app.main import create_app
from app.storage import LocalStore
from app.worker import Worker
from tests.fake_engine import FakeEngine

PUBLIC_RESOLVER = lambda host, port: ["93.184.216.34"]  # noqa: E731


def make_zip(files: dict[str, str | bytes] | None = None) -> bytes:
    files = files or {"app/main.py": "print('hi')\n", "README.md": "# demo\n"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, c in files.items():
            zf.writestr(n, c)
    return buf.getvalue()


def make_targz(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for n, c in files.items():
            c = c.encode() if isinstance(c, str) else c
            ti = tarfile.TarInfo(n)
            ti.size = len(c)
            tf.addfile(ti, io.BytesIO(c))
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(environment="test", database_url=f"sqlite:///{tmp_path}/t.db", store_dir=str(tmp_path / "store"),
                 scratch_dir=str(tmp_path / "scratch"), jwt_secret="x" * 48, rate_limit_enabled=False, log_json=False,
                 log_level="WARNING", model_api_key="sk-test-SECRET-KEY-123", engine_workers=1, sse_poll_s=0.05,
                 job_backoff_base_s=0.0, max_upload_mb=5, max_extracted_mb=20, max_user_doc_mb=1)
    set_settings(s)
    return s


@pytest.fixture
def fake(monkeypatch) -> FakeEngine:
    f = FakeEngine()
    engine_mod.set_engine(f)
    yield f
    engine_mod.set_engine(None)


@pytest.fixture
def db(settings) -> Database:
    d = Database(settings.database_url)
    d.create_all()
    yield d
    d.dispose()


@pytest.fixture
def store(settings) -> LocalStore:
    return LocalStore(settings.store_dir)


@pytest.fixture
def app(settings, db, store, fake):
    billing.set_billing(billing.NoBilling())
    return create_app(settings, db=db, store=store, resolver=PUBLIC_RESOLVER)


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def worker(settings, db, store, fake) -> Worker:
    return Worker(settings, db=Database(settings.database_url), store=store, worker_id="test-worker")


class User:
    def __init__(self, client, email):
        self.client, self.email = client, email
        r = client.post("/v1/auth/signup", json={"email": email, "password": "correct horse battery"})
        assert r.status_code == 201, r.text
        self.token = r.json()["access_token"]
        self.id = r.json()["user"]["id"]
        self.h = {"Authorization": f"Bearer {self.token}"}
        me = client.get("/v1/me", headers=self.h).json()
        self.wid = me["workspaces"][0]["id"]

    def req(self, method, path, **kw):
        kw.setdefault("headers", {})
        kw["headers"] = {**self.h, **kw["headers"]}
        return self.client.request(method, path, **kw)

    def get(self, p, **kw): return self.req("GET", p, **kw)
    def post(self, p, **kw): return self.req("POST", p, **kw)
    def patch(self, p, **kw): return self.req("PATCH", p, **kw)
    def delete(self, p, **kw): return self.req("DELETE", p, **kw)

    def project(self, name="proj", with_source=True, wid=None) -> str:
        r = self.post(f"/v1/workspaces/{wid or self.wid}/projects", json={"name": name})
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        if with_source:
            r = self.post(f"/v1/projects/{pid}/source", files={"file": ("src.zip", make_zip(), "application/zip")})
            assert r.status_code == 201, r.text
        return pid

    def start_run(self, pid, **body):
        body = {"type": "analyze", "model_profile": "balanced", "max_usd": 5, **body}
        r = self.post(f"/v1/projects/{pid}/runs", json=body)
        assert r.status_code in (200, 202), r.text
        return r.json()


@pytest.fixture
def alice(client): return User(client, "alice@example.com")


@pytest.fixture
def bob(client): return User(client, "bob@example.com")


def drain(worker: Worker, limit: int = 10) -> int:
    n = 0
    while n < limit and worker.run_once():
        n += 1
    return n
