# migration-agent backend

FastAPI + SQLAlchemy 2 + Pydantic v2 service and DB-backed worker for the hosted product. Implements
`docs/platform-contract.md` sections 3 to 6. Billing is a seam only (`app/billing.py`): no payment code.

## Layout

```
app/main.py          app factory (uvicorn --factory app.main:app_factory)
app/routers/         auth, workspaces (+members, usage, entitlements, audit), projects (+source, user-docs, estimate),
                     runs (+SSE), results (graph, document, export, findings, comments)
app/tenancy.py       the ONE tenancy boundary (scoped_select + workspace/project/run dependencies)
app/safety.py        SSRF guard, safe archive extraction, filename sanitising
app/gitfetch.py      hardened shallow clone (worker/estimate side)
app/worker.py        python -m app.worker
app/jobs.py          queue (FOR UPDATE SKIP LOCKED on Postgres, atomic UPDATE..RETURNING on SQLite)
app/engine.py        adapter to migration_agent.service (lazy import, injectable for tests)
app/profiles.py      model_profile -> engine config (the only mapping), server-side API key injection
app/billing.py       BillingProvider, NoBilling, plans/entitlements, can_start_run
app/storage.py       ObjectStore, LocalStore, S3Store skeleton
migrations/          Alembic (0001 schema, 0002 Postgres RLS)
tests/               pytest (no network, no LLM)
```

## Run locally (SQLite, dev auth, local object store)

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/pip install -e ..            # the engine (migration_agent.service); needed by the worker
export DATABASE_URL=sqlite:///./dev.db STORE_DIR=./data/store JWT_SECRET=$(openssl rand -hex 32)
export MODEL_API_KEY=...               # server-side only; omit and set ENGINE_OFFLINE=1 for a model-free demo
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app_factory --factory --reload      # API on :8000, docs at /docs (not in production)
.venv/bin/python -m app.worker                                   # in a second shell
```

Production: `DATABASE_URL=postgresql://...` (psycopg 3), `ENVIRONMENT=production` (requires `JWT_SECRET` for dev auth,
disables /docs), `STORE_BACKEND=s3` (+ `S3_BUCKET`, needs `pip install '.[s3]'`; skeleton, untested), run
`alembic upgrade head` as a release step. Run the API as a non-owner DB role so the RLS policies bite (see NOTES).

## Configuration (environment)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./dev.db` | `postgresql://` or `sqlite:///` |
| `AUTH_MODE` | `dev` | `dev` password+JWT (argon2, HS256) or `oidc` |
| `JWT_SECRET`, `JWT_TTL_MINUTES` | random per process (dev only), 60 | dev issuer |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`, `OIDC_ALGORITHMS` | | external JWT verification (asymmetric only) |
| `SIGNUP_ENABLED` | `true` | dev-mode signup |
| `STORE_BACKEND`, `STORE_DIR`, `S3_*` | local, `./data/store` | object store |
| `MODEL_API_KEY`, `MODEL_API_BASE` | | injected per job into the engine config, never stored |
| `PROFILE_FAST_MODEL`, `PROFILE_BALANCED_MODEL`, `PROFILE_BEST_MODEL` | gemini 2.5 lite/flash/pro | profile -> model ids |
| `DEFAULT_MAX_USD`, `HARD_MAX_USD` | 5, 200 | per-run budget default / ceiling |
| `MAX_UPLOAD_MB`, `MAX_EXTRACTED_MB`, `MAX_FILE_MB`, `MAX_ARCHIVE_FILES`, `MAX_COMPRESSION_RATIO` | 100, 500, 50, 50000, 200 | upload safety |
| `GIT_TIMEOUT_S`, `GIT_MAX_MB`, `GIT_ALLOWED_HOSTS` | 120, 300, empty (any public host) | git ingestion |
| `RATE_LIMIT_ENABLED`, `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_AUTH_PER_MINUTE`, `TRUST_PROXY_HEADERS` | true, 300, 20, false | in-process token bucket |
| `JOB_MAX_ATTEMPTS`, `JOB_BACKOFF_BASE_S`, `JOB_LOCK_TIMEOUT_S`, `SCRATCH_DIR`, `ENGINE_OFFLINE`, `ENGINE_WORKERS` | 3, 10, 600, tmp, false, 2 | worker |
| `CORS_ORIGINS` | empty | comma separated |

## Tests

```bash
.venv/bin/python -m pytest                       # whole suite (about 20 s)
.venv/bin/python -m pytest --cov=app --cov-report=term-missing
```

`tests/test_real_engine.py` runs the real engine offline on `eval/repos/django-lite` (skipped if the engine is not
installed in this venv). Everything else uses `tests/fake_engine.py`, which has the contract section 2 signatures.

## API notes

* Errors: `{"error":{"code","message"}}`. Lists: `{"items","total"}` with `limit` (default 50, max 200), `offset`.
* A resource of another workspace is always 404. Insufficient role inside your workspace is 403.
* `POST /v1/projects/{pid}/runs` is 202; send `Idempotency-Key` to make retries safe (replay returns 200 and the same run).
* `GET /v1/runs/{rid}/events` is SSE: event names `status|stage|progress|usage|log|end`, `id` = sequence, resume with
  `?after=` or `Last-Event-ID`.
