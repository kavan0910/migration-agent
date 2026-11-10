# deploy/ notes (stream D)

## Built
- `docker-compose.yml`: postgres (volume, healthcheck), api, worker, egress-proxy (reuses `docker/squid.conf`), web, optional `minio` (profile `s3`). Worker hardened: non-root, read-only rootfs, caps dropped, no-new-privileges, pids/memory limits, noexec tmpfs scratch, internal-only network, egress only via allowlist proxy. Model key only in the worker.
- `Dockerfile.backend`, `Dockerfile.web`: multi-stage, non-root uid 10001, base images via ARG, no secrets baked (only public `NEXT_PUBLIC_*` build args).
- `.env.example` (placeholders only), `smoke.sh`.
- `.github/workflows/{backend,web,security}.yml` (engine job in `ci.yml` untouched).
- `docs/security/*`, `SECURITY.md`, `docs/operations-runbook.md`; README and CHANGELOG updated.

## Validated
- Docker is not installed on this machine: `docker compose config`, image builds and `smoke.sh` were NOT run.
- Compose and workflow YAML parse with Ruby's YAML loader (anchors/merge keys resolve; worker `read_only` true). `smoke.sh` passes `bash -n`.
- Nothing was run against backend/ or web/ (not present when written).

## Not built
- TLS ingress / reverse proxy, production secret manager, monitoring/alerting, log shipping, image signing for platform images, S3 store wiring (minio is only a placeholder service), ephemeral per-run worker containers.

## Reconciled with backend/ (read at time of writing; backend routers and app.main were not yet present)
- Env names follow `backend/app/config.py` (`STORE_BACKEND`, `STORE_DIR`, `SCRATCH_DIR`, `MODEL_API_KEY`, `MODEL_API_BASE`, `PROFILE_*_MODEL`, `MAX_EXTRACTED_MB`, `GIT_ALLOWED_HOSTS`, `SIGNUP_ENABLED`, ...). `DATABASE_URL` uses `postgresql+psycopg://`; confirm the backend depends on psycopg 3.
- Verified against the running backend (see `scripts/e2e_local.sh`): the ASGI entrypoint is the factory `app.main:app_factory` (`--factory`; there is no `app` object), worker `python -m app.worker`, `/healthz` and `/readyz` (200 `{status}`), Alembic via `backend/alembic.ini` + `backend/migrations` (now copied to `/app` in the image; the api command runs `alembic upgrade head` before uvicorn). Still assumed: `TEST_DATABASE_URL` in backend tests, backend `[dev]` extra with ruff/mypy/pytest.
- Smoke (field names confirmed: signup 201 `access_token`, `GET /v1/workspaces` `items[0].id` is the default workspace created at signup, upload field `file` 201, passwords >= 10 chars): `GET /v1/workspaces` lists a default workspace (else creates one); project create 201, source upload multipart field `file` 201, delete 204.
- Web: `web/` has no `public/` dir (Dockerfile creates one); `next start` with production node_modules (no `output: standalone`); scripts lint/typecheck/test/build exist.
- Default model profiles are Gemini, so `MODEL_ENDPOINT_HOST` defaults to `generativelanguage.googleapis.com`; change both together.

## Open problems
- Worker holds DB credentials (queue) and is long-lived: deviates from SEC-2 (ephemeral, no DB creds). OPEN (pentest F5).
- API container has unrestricted outbound access (git clones for /estimate, OIDC JWKS); SSRF relies on app-level checks. The WORKER also clones (analyze runs), but through the egress proxy: `deploy/squid.conf` allows CONNECT :443 to the model host and `GIT_ALLOWED_HOSTS` only (empty = worker cannot clone).
- Squid allows by domain only (DNS/model host covert channel, as in the engine threat model).
- minio uses `latest`; postgres/squid images are tag-pinned, not digest-pinned.
- Actions `startup_failure` is unresolved; all CI is unverified.
- gitleaks is downloaded by version in `security.yml` without checksum verification.
- No real retention job (auto-purge) exists; periods in the policy are proposals.

## Pentest follow-up (2026-11-09), all UNVERIFIED (no Docker/Postgres here; compose only parsed as YAML)
- Database roles: bootstrap superuser (init only) / `ma_owner` (schema owner, `migrate` one-shot service only) / `ma_app` (api + worker, NOBYPASSRLS). `deploy/postgres-init/10-roles.sh` runs on first init of the volume only; an existing volume needs the roles created by hand. New required env: `MA_OWNER_PASSWORD`, `MA_APP_PASSWORD`.
- Migrations moved out of the api command into the `migrate` service (`MIGRATION_DATABASE_URL`).
- Image sets `ENVIRONMENT=production` and `MA_IN_CONTAINER=1` (app refuses to start if `ENVIRONMENT` goes missing); production rejects weak/placeholder `JWT_SECRET`, so a copied `.env.example` will not boot until secrets are generated.
- `SIGNUP_ENABLED` defaults to false; `deploy/smoke.sh` opens it for its own run. `TRUST_PROXY_HEADERS=false` / `TRUSTED_PROXY_COUNT=1`: set true only behind your own proxy that appends to X-Forwarded-For; uvicorn's own proxy-header rewriting is off.
- API `/scratch` tmpfs (`API_SCRATCH_SIZE`, 2g) sized for 3 estimate slots x (upload + extracted limits); /tmp stays 256m.
- Squid for this stack is `deploy/squid.conf` (generated allowlist file for git hosts); `docker/squid.conf` is unchanged.
