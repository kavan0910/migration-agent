"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from . import __version__
from .billing import ensure_default_plans
from .config import Settings, get_settings
from .db import Database
from .deps import throttle
from .errors import install_handlers
from .logging_setup import BodyLimitMiddleware, RequestIdMiddleware, configure_logging
from .ratelimit import TokenBucket
from .routers import auth, projects, results, runs, workspaces
from .safety import default_resolver
from .security import TokenVerifier, make_verifier
from .storage import ObjectStore, make_store


def create_app(settings: Settings | None = None, *, db: Database | None = None, store: ObjectStore | None = None,
               verifier: TokenVerifier | None = None, resolver=None, auto_create_schema: bool = False) -> FastAPI:
    st = settings or get_settings()
    configure_logging(st.log_level, st.log_json)
    database = db or Database(st.database_url)
    if auto_create_schema:
        database.create_all()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with database.session() as s:
            try:
                ensure_default_plans(s)
            except Exception:  # schema not migrated yet; /readyz will report it
                pass
        yield

    app = FastAPI(title="migration-agent backend", version=__version__, lifespan=lifespan,
                  dependencies=[Depends(throttle)], docs_url="/docs" if st.environment != "production" else None,
                  redoc_url=None, openapi_url="/openapi.json" if st.environment != "production" else None)
    app.state.settings = st
    app.state.db = database
    app.state.store = store or make_store(st)
    app.state.verifier = verifier or make_verifier(st)
    app.state.resolver = resolver or default_resolver
    app.state.ip_bucket = TokenBucket(st.rate_limit_per_minute * 3)
    app.state.user_bucket = TokenBucket(st.rate_limit_per_minute)
    app.state.auth_bucket = TokenBucket(st.rate_limit_auth_per_minute)
    install_handlers(app)
    app.add_middleware(BodyLimitMiddleware, default_limit=1024 * 1024,
                       upload_limit=(st.max_upload_mb + 2) * 1024 * 1024)
    if st.cors_list:
        app.add_middleware(CORSMiddleware, allow_origins=st.cors_list, allow_credentials=False,
                           allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                           expose_headers=["X-Request-Id"])
    app.add_middleware(RequestIdMiddleware)

    @app.get("/healthz", include_in_schema=False, dependencies=[])
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False, dependencies=[])
    def readyz():
        from fastapi.responses import JSONResponse
        try:
            with database.engine.connect() as c:
                c.execute(text("SELECT 1 FROM workspaces LIMIT 1"))
            return {"status": "ready"}
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)

    for r in (auth, workspaces, projects, runs, results):
        app.include_router(r.router)
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
