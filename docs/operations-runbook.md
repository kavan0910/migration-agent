# Operations runbook

Scope: the single-host compose deployment in `deploy/`. Written before the stack has run end to end; commands that
depend on `backend/` internals (migration tool, table names) are marked **[assumed]** and must be confirmed.
All commands run from the repo root. Alias for brevity:

```bash
alias dc='docker compose -f deploy/docker-compose.yml --env-file deploy/.env'
```

## 1. Deploy

First time:
```bash
cp deploy/.env.example deploy/.env      # then set POSTGRES_PASSWORD, MA_OWNER_PASSWORD, MA_APP_PASSWORD, JWT_SECRET (openssl rand -hex 32; the backend refuses placeholders and weak values in production), MODEL_API_KEY, MODEL_ENDPOINT_HOST, GIT_ALLOWED_HOSTS (worker git clones go through the egress proxy and only reach these hosts); signup is closed unless SIGNUP_ENABLED=true
dc config -q                            # static validation
dc up -d --build
dc ps                                   # api, web, postgres should be "healthy"
deploy/smoke.sh                         # or SMOKE_NO_UP=1 deploy/smoke.sh against the running stack
```
Upgrade: `git pull`, `dc build`, run migrations (section 3), `dc up -d`. Roll back by checking out the previous
tag and repeating; restore the database only if a migration was destructive (take a backup first, section 2).
Pin base images by digest for any release (`PYTHON_IMAGE`, `NODE_IMAGE` in `.env`). The stack publishes ports on
127.0.0.1 only: put a TLS-terminating reverse proxy in front for anything non-local (**not provided**).

## 2. Backups and restore test

Postgres (logical dump):
```bash
dc exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > backup-$(date -u +%F).dump
```
Object store (`objects` volume, LocalStore):
```bash
docker run --rm -v migration-agent-deploy_objects:/data:ro -v "$PWD":/out busybox tar -czf /out/objects-$(date -u +%F).tgz -C /data .
```
(The volume name is prefixed by the compose project name; check with `docker volume ls`.) Encrypt backups before they
leave the host and store them off-host. Retention: see `docs/security/data-handling-and-retention.md`.

**Restore test (do this monthly and before trusting backups; never yet performed):**
1. Start a scratch stack with a different project name: `COMPOSE_PROJECT_NAME=ma-restore dc up -d postgres`.
2. `dc exec -T postgres pg_restore -U ma_app -d migration_agent --clean --if-exists < backup.dump`.
3. Restore objects into the scratch `objects` volume, start `api`, run `SMOKE_NO_UP=1 deploy/smoke.sh`.
4. Record date, backup age, duration, result. Tear down with `dc down -v`.
Deleted customer data remains in backups until they expire: this is why retention is stated in the policy.

Deletion verification (staging, after deleting a project; table list **[assumed]**, replace with the real schema):
```bash
dc exec -T postgres psql -U ma_app migration_agent -c "select count(*) from runs where project_id='<pid>'"
dc exec -T api sh -c 'find "$STORE_DIR" -path "*<pid>*" | head'
```
Both must return zero rows / nothing.

## 3. Migrations
**[assumed]** the backend uses Alembic: `dc run --rm api alembic upgrade head`. Rules: take a backup first; migrations
must be backward compatible with the previous release for one deploy (expand, deploy, contract); never edit an
applied migration. Confirm the actual command in `backend/NOTES.md` when it exists.

## 4. Scaling
- API: stateless; run more replicas behind a proxy (compose: `dc up -d --scale api=2` requires removing the fixed host port mapping first).
- Worker: `dc up -d --scale worker=N`. Each worker claims jobs with `SKIP LOCKED`. Throughput is bounded by model rate limits (SCL-1), not CPU: raise `ENGINE_WORKERS`/replicas only while 429s stay rare.
- Postgres: single instance; move to a managed service before real customers. Object store: LocalStore is single host; use S3 (skeleton only) for multi host.
- Large repos: always estimate first (`POST .../estimate`), keep per-run `max_usd` set.

## 5. Rotating keys
| Secret | Procedure | Effect |
|---|---|---|
| Model provider key (`MODEL_API_KEY`) | Create new key at provider, set in `deploy/.env`, `dc up -d worker`, revoke old key | In-flight run may fail and retry |
| `JWT_SECRET` (dev auth) | Change, `dc up -d api` | All sessions invalidated |
| `POSTGRES_PASSWORD` | `ALTER USER` in Postgres, then update `.env`, recreate api and worker | Brief outage |
| OIDC settings | Keys rotate at the IdP (JWKS); update `OIDC_*` only on issuer change | None normally |
Suspected leak: rotate first, investigate second (`docs/security/incident-response.md`). Never paste secrets into
tickets or chat. Confirm with `git log -S` / gitleaks that nothing was committed.

## 6. On-call basics
- First look: `dc ps`, `dc logs --tail=100 api worker`, `curl localhost:8000/readyz`.
- Proxy decisions: `dc logs egress-proxy` (denied model host = wrong `MODEL_ENDPOINT_HOST`).
- Stuck runs: look for `running` runs with old heartbeat; the queue `jobs` table has `locked_at`/`attempts` **[assumed]**; restarting the worker releases/reclaims per backend implementation (confirm).
- Escalation: owner. Incident handling: `docs/security/incident-response.md`.
- Keep a dated log of every manual production action.

## 7. SLO suggestions (starting points, not commitments)
| SLI | Suggested objective |
|---|---|
| API availability (`/readyz` success, 5 min windows) | 99.5% monthly |
| API p95 latency, non-run endpoints | < 500 ms |
| Run start latency (queued -> running) | p95 < 2 min at normal load |
| Run success rate excluding user budget stops | > 95% |
| Backup freshness | newest verified backup < 24 h |
Measure before promising anything externally; there is no monitoring in the stack yet.

## 8. Cost monitoring
Model spend dominates (COST-1). Sources: `usage_events` (tokens, USD per run/stage/model) via
`GET /v1/workspaces/{wid}/usage`; the provider's own dashboard is the check against drift. Controls: per-run
`max_usd`, `DEFAULT_MAX_USD`/`HARD_MAX_USD`, entitlement limits, provider-side spend cap. Review weekly: spend per
workspace, cost per 1k lines against Phase 0 numbers (`docs/phase0-results.md`), cache hit rate, retries. Alert
**[not built]** on daily spend above a threshold; set the provider cap as the backstop. Infrastructure costs: disk
growth of the `objects` volume and Postgres.

## 9. Common failures
| Symptom | Likely cause | Fix |
|---|---|---|
| `dc config` error "set POSTGRES_PASSWORD" | missing `.env` values | copy `.env.example`, set required values |
| api unhealthy, logs show DB connection refused | Postgres not ready or wrong password | `dc logs postgres`; password changed after the volume was initialised (the volume keeps the old one) |
| Run stuck `queued` | worker down or cannot reach DB | `dc ps worker`, `dc logs worker` |
| Run fails with connection error to model | proxy denies host | set `MODEL_ENDPOINT_HOST` to the exact host of `MODEL_API_BASE`, `dc up -d egress-proxy` |
| 429 / 503 from provider | rate limits | lower concurrency; engine retries with backoff |
| `stopped_budget` | per-run `max_usd` reached | raise budget and resume (cache and checkpoints make it cheap) |
| Worker `OSError: Read-only file system` | engine writing outside `/scratch` or `/tmp` | set `SCRATCH_DIR`; report as backend bug, do not drop `read_only` |
| Web shows network errors | `NEXT_PUBLIC_API_URL` baked wrong at build | rebuild web with the correct public URL; check `CORS_ORIGINS` |
| Volume permission denied on `/data` | volume created by another uid | `docker run --rm -v <vol>:/data busybox chown -R 10001:10001 /data` |

## 10. GitHub Actions `startup_failure` (troubleshooting note; NOT resolved)
The repository's recent Actions runs end in `startup_failure`, meaning the run never started: jobs did not execute, so
this is not a test failure. The YAML has not been shown to be the cause (the files parse). Candidate causes to check
in this order, none confirmed:
1. Account/billing state: Settings > Billing; spending limit or payment issue can block runs, especially for private repos.
2. Actions disabled or restricted: repo Settings > Actions > General (allowed actions policy; `aquasecurity/*`, `docker/*`, `sigstore/*`, `anchore/*` must be permitted if "selected actions" is set).
3. Workflow permissions: Settings > Actions > General > Workflow permissions; `ci.yml` requests `packages: write` and `id-token: write` on the image job, which an org/enterprise policy may forbid.
4. Org/enterprise policy that requires pinning actions to full commit SHAs (the workflows use version tags).
5. Look at the run page annotation or `gh run view <id> --log`/`gh api repos/<o>/<r>/actions/runs/<id>` for the exact message.
Until a run succeeds, treat every CI job in this repo as **unverified** and run the same commands locally.
