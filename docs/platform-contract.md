# Platform contract (hosted product)

Single source of truth shared by the parallel workstreams. Implements `product-architecture.md` Phases 1 to 3,
**except billing**: no payment provider, no checkout, no invoices. Billing stays a `BillingProvider` interface with a
`NoBilling` implementation (decision deferred by the owner).

Workstreams and owned directories (do not edit another stream's directory):

| Stream | Owns | Depends on |
|---|---|---|
| A. Engine seams | `src/migration_agent/` (new `service.py`, `storage.py`; minimal edits elsewhere), `tests/` for those | nothing |
| B. Backend | `backend/` | `migration_agent.service` (section 2) |
| C. Web | `web/` | HTTP API (section 3) |
| D. Ops, security, docs | `deploy/`, `docs/security/`, `.github/workflows/`, root `*.md` additions | paths of A, B, C |

## 1. Repository layout

```
src/migration_agent/        engine (library + CLI), stays pip-installable and CLI-compatible
backend/                    FastAPI service + worker (own pyproject.toml, own venv at backend/.venv)
web/                        Next.js (App Router, TypeScript, Tailwind)
deploy/                     docker-compose for the full stack, Dockerfiles, env templates
docs/security/              data-handling, retention, subprocessor list, SOC 2 readiness checklist
```

## 2. Engine service API (`migration_agent.service`, stream A)

Pure functions over a local **project directory** (the existing folder layout). No HTTP, no database, no network
other than the configured model endpoint. All must be importable without the `llm` extra unless an LLM is used.

```python
class StageResult(BaseModel):
    stage: str                        # ingest | extract | analyze | verify | resolve
    status: Literal["ok", "stopped_budget", "failed"]
    summary: dict                     # stage specific counts (nodes, edges, findings, chunks ...)
    cost_usd: float
    error: str | None = None

Event = dict   # {"type":"progress","stage":str,"done":int,"total":int}
               # {"type":"usage","stage":str,"task":str,"model":str,"input_tokens":int,"output_tokens":int,"usd":float}
               # {"type":"log","level":str,"message":str}

def run_stage(project_dir: Path, stage: str, *, source: str | None = None, config: dict | None = None,
              max_usd: float | None = None, workers: int = 1, incremental: bool = False, offline: bool = False,
              on_event: Callable[[Event], None] | None = None, llm: object | None = None,
              finding_id: str | None = None, status: str | None = None, note: str | None = None,
              reanalyze: bool = False) -> StageResult
def estimate(project_dir: Path, config: dict | None = None, incremental: bool = False) -> dict   # {"chunks":int,"usd":float}
def review_state(project_dir: Path) -> dict        # same shape the review UI serves at /api/state
def graph_snapshot(project_dir: Path, version: int | None = None) -> dict   # {"version","nodes":[...],"edges":[...]}
def graph_versions(project_dir: Path) -> list[dict]
def graph_diff(project_dir: Path, a: int, b: int) -> dict
def add_comment(project_dir: Path, item_id: str, text: str, author: str | None = None) -> dict
def add_resolution(project_dir: Path, finding_id: str, status: str, note: str = "", author: str | None = None) -> dict
def document(project_dir: Path) -> str            # docs/understanding.latest.md or ""
def changelog(project_dir: Path) -> str
def pack_project(project_dir: Path, *, include_cache: bool = True) -> bytes   # deterministic .tar.gz
def unpack_project(data: bytes, dest: Path) -> None                           # validates paths, no traversal
```

Rules: `on_event` is called from worker threads safely; budget is enforced inside the engine (`max_usd`); the engine
never reads credentials from the process environment when `config` carries an explicit `api_key`; `llm` allows tests to
inject `FakeLLM`/`OfflineLLM`; the CLI keeps working unchanged; the existing 582 tests stay green.

## 3. HTTP API (`/v1`, stream B implements, stream C consumes)

JSON, ids are UUID strings, timestamps ISO-8601 UTC, auth `Authorization: Bearer <jwt>`.
Errors: `{"error":{"code":"string","message":"string"}}` with proper status codes (401, 403, 404, 409, 422, 429).
Lists: `{"items":[...],"total":int}` with `limit` (default 50, max 200) and `offset`.
A resource of another workspace is always answered with **404**, never 403, to avoid leaking existence.

| Area | Endpoint | Notes |
|---|---|---|
| Health | `GET /healthz`, `GET /readyz` | no auth |
| Auth | `POST /v1/auth/signup`, `POST /v1/auth/login`, `GET /v1/me` | `AUTH_MODE=dev` (password, local JWT) or `oidc` (verify external JWT via JWKS; users are created on first sight) |
| Workspaces | `GET/POST /v1/workspaces`, `GET /v1/workspaces/{wid}`, `GET/POST /v1/workspaces/{wid}/members`, `DELETE .../members/{uid}` | roles: owner, admin, member, viewer |
| Projects | `POST/GET /v1/workspaces/{wid}/projects`, `GET/PATCH/DELETE /v1/projects/{pid}` | DELETE removes every artifact, cache entry and row, writes an audit record |
| Source | `POST /v1/projects/{pid}/source` (multipart zip/tar.gz **or** JSON `{"git_url":"https://..."}`) | git URL: https only, public hosts only, block private/link-local/loopback (SSRF), size caps |
| User docs | `POST/GET/DELETE /v1/projects/{pid}/user-docs` | for the Verifier only |
| Estimate | `POST /v1/projects/{pid}/estimate` | `{chunks, usd, allowed, reason}` using the engine dry-run and the entitlement check |
| Runs | `POST /v1/projects/{pid}/runs` (202), `GET /v1/projects/{pid}/runs`, `GET /v1/runs/{rid}`, `POST /v1/runs/{rid}/cancel`, `GET /v1/runs/{rid}/events` (SSE) | body `{type: analyze|verify|reanalyze, model_profile: fast|balanced|best, max_usd, incremental}`; status: queued, running, succeeded, failed, stopped_budget, cancelled |
| Results | `GET /v1/projects/{pid}/graph` (`version,type,q,limit,offset`), `.../graph/nodes/{nid}`, `.../graph/versions`, `.../graph/diff?a=&b=`, `.../document`, `.../changelog`, `.../export?format=md|json|html` | |
| Review | `GET /v1/projects/{pid}/findings`, `POST .../findings/{fid}/resolution`, `GET/POST .../comments` | resolution status: accepted_doc, accepted_code, dismissed, needs_reanalysis; `needs_reanalysis` and `accepted_doc` enqueue a `reanalyze` run |
| Usage | `GET /v1/workspaces/{wid}/usage?from=&to=`, `GET /v1/workspaces/{wid}/entitlements` | totals and per run; plan, limits, consumption |
| Audit | `GET /v1/workspaces/{wid}/audit` | admins only |

`model_profile` maps server-side to engine model configs; customers never choose raw model ids.

## 4. Data model (stream B, Postgres in production, SQLite in tests)

workspaces, users, memberships(role), projects, sources, user_docs, runs(status, type, profile, max_usd,
cost_usd, engine_version, started_at, finished_at, error), run_events(seq, type, payload), artifacts(run, kind, key,
sha256, bytes), usage_events(workspace, project, run, stage, task, model, input_tokens, output_tokens, usd, at),
plans(name, limits json), entitlements(workspace, plan, overrides json), audit_log(actor, action, target, at, meta),
jobs(queue table: id, run_id, status, locked_by, locked_at, attempts, available_at).

Tenancy: every project-scoped query filters by the caller's workspace membership in **one shared dependency**; no
route builds its own filter. Postgres deployments additionally enable row-level security keyed on `workspace_id`.

## 5. Worker (stream B)

`python -m app.worker`: claims jobs (`FOR UPDATE SKIP LOCKED` on Postgres, equivalent on SQLite), materialises the
project from storage into a scratch dir, calls `migration_agent.service.run_stage` per stage, streams events into
`run_events` and `usage_events`, uploads artifacts, enforces the per-run budget and entitlements, retries with
backoff, is idempotent per run, and always cleans the scratch dir. The model API key is injected per job from
server configuration, never stored in a project.

Storage interface `ObjectStore` (put, get, delete_prefix, list) with `LocalStore` (dev, tests) and an `S3Store`
skeleton behind the same interface. Keys are prefixed `{workspace_id}/{project_id}/`. LLM cache is per workspace.

## 6. Billing-agnostic seam (stream B)

`BillingProvider` protocol (`ensure_customer`, `sync_plan`, `report_usage`) and `NoBilling` (no-ops). `can_start_run(
workspace, estimate)` is the only place plan limits are enforced. Default plan `internal` is generous. No payment
code, SDK, keys, webhooks or pricing numbers anywhere.

## 7. Web (stream C)

Next.js App Router, TypeScript strict, Tailwind. Pages: landing, sign up / log in, dashboard (projects), new project
(connect git URL or upload), project overview (runs, estimate, start run with live progress via SSE), understanding
document, graph explorer (search, filter by type, node detail with evidence quotes and confidence, version diff),
findings review (resolve, comment), usage and plan, workspace members, settings (delete project/data). Typed API
client generated by hand from section 3 with one module (`web/lib/api.ts`). `NEXT_PUBLIC_API_MOCK=1` serves
realistic fixtures so the whole UI can be demoed and tested without the backend. Accessible, responsive, light and
dark. `npm run build`, `npm run lint` and `npm run typecheck` must pass.

## 8. Ops, security, docs (stream D)

`deploy/docker-compose.yml` (postgres, backend, worker with egress allowlist proxy, web), Dockerfiles for backend and
web, `.env.example`, a smoke script, CI jobs for backend and web, `docs/security/` (data handling and retention,
subprocessors, incident response outline, SOC 2 readiness checklist, secure SDLC), an operations runbook, and updates
to README and CHANGELOG. No claims of certifications that do not exist.

## 9. Definition of done

Each stream: tests pass locally, no secrets committed, nothing outside its owned directories modified (except stream A's
engine and the CHANGELOG/README entries by D), a short `NOTES.md` in its directory listing what was built, what is
deliberately not built, and open problems.
