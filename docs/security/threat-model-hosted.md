# Threat model update: hosted product

Extends `docs/threat-model.md` (engine, single user) to the multi-tenant platform in `docs/platform-contract.md`.
Status: design-level analysis plus the fixes from the 2026-11-09 hostile review (`pentest-2026-11-09.md`, which lists what is
fixed with tests, UNVERIFIED and OPEN). Controls are marked **designed** (in contract/compose), **verify** (confirm in code)
or **missing**. There has been no independent penetration test.

## New assets
Other tenants' source and results; the shared database; object store; model provider API key; JWT signing secret;
audit log; the egress allowlist.

## New actors
Malicious authenticated tenant; a tenant uploading hostile repositories; a curious operator; an attacker with only
network access to the API; a compromised dependency.

## Trust boundaries
Browser -> web -> API (JWT) -> Postgres / object store; API -> queue -> worker; worker -> egress proxy -> model host;
API -> internet (git clone). The worker processes hostile bytes and is the highest-risk component.

## Threats

### T-H1 Tenant isolation (SEC-1)
- Threat: IDOR, missing filter on one route, shared cache or object key collisions leak data across workspaces.
- Controls: one shared membership dependency for every project-scoped query (contract s4) **verify**; 404 not 403 for foreign resources **verify**; Postgres RLS keyed on `workspace_id` **verify**; object keys prefixed `{workspace}/{project}/` **verify**; LLM cache per workspace (identical code in two tenants must not share responses) **verify**; smoke test checks foreign project returns 404 (`deploy/smoke.sh`).
- Update 2026-11-09: tenancy tests exist for every route. Compose now connects as a non-superuser `ma_app` and RLS is FORCED (migration 0004), but this is **UNVERIFIED** (never run on Postgres). Still missing: RLS on memberships/audit_log/entitlements, composite (project, workspace) foreign keys.

### T-H2 Worker sandbox (SEC-2)
- Threat: hostile repo content exploits a parser (tree-sitter, pypdf, XML) or the engine to escape, reach the database, or exfiltrate.
- Controls: non-root, read-only rootfs, all capabilities dropped, no-new-privileges, pids and memory limits, noexec tmpfs scratch, no direct route out (internal network; only the allowlist proxy reaches `MODEL_ENDPOINT_HOST:443`) **designed in compose**; code is never executed (engine property); scratch cleaned per job.
- Gaps (stated plainly, pentest F5 **OPEN**): the compose worker is long-lived and handles many runs and **has database credentials** (needed for the queue). SEC-2 asks for one ephemeral container per run with no DB credentials; that is **not built**. Mitigation path: a supervisor that launches a fresh container per run and passes artifacts through object storage with a scoped token. Containers share a kernel; stronger isolation (gVisor/Firecracker) is **not built**. Proxy filters by domain; DNS and the model host itself are covert channels (threat-model residual 5).

### T-H3 SSRF and hostile archives (SEC-4)
- Threat: `git_url` pointed at internal addresses or metadata endpoints; redirects to private IPs; DNS rebinding; zip/tar bombs and path traversal.
- Controls: https only, public hosts only, block private/link-local/loopback **verify in backend**; archive size and member caps; `unpack_project` validates paths **verify in engine**; clones use hardened flags (no hooks, no LFS filters).
- Update: archive entries (directories included), depth and time are capped; cloned trees are capped; the URL given to git is rebuilt from validated components; IPv6-embedded IPv4 forms are blocked; the worker clones through an allowlisting proxy (`GIT_ALLOWED_HOSTS`). Gaps: the API container has general outbound access (it clones for /estimate). Resolve-then-connect pinning against DNS rebinding and revalidating redirects must be implemented and tested **verify**. Better: perform clones in the sandboxed worker through a dedicated git-only egress proxy **not built**.

### T-H4 Prompt injection (SEC-6)
- Threat: repo content steers the model to emit misleading claims or try tool abuse.
- Controls: untrusted-data delimiters, neutralisation, path-jailed read-only tools, deterministic evidence verification of every claim (output gate), canary tests in eval. Residual: true-but-misleading claims remain possible (engine threat model residual 2). In the hosted product the model has no network or write tools, and one tenant's prompt can never include another tenant's data because jobs are per project.

### T-H5 LLM provider data exposure (SEC-3)
- Threat: provider retains or trains on code; provider breach.
- Controls: redaction at ingest; per-workspace cache; budget limits. Gaps: **no zero-retention agreement exists**; subprocessor not chosen (`subprocessors.md`); BYO-key/self-hosted endpoint tier (`MA_LLM_API_BASE`) not built as a product feature. Customer-facing text must say code excerpts are sent to a third party.

### T-H6 Secrets (SEC-5, SEC-7)
- Threat: provider key or JWT secret leakage; secrets in customer repos reaching the model or logs.
- Controls: key injected into the worker only (api and web get none); `.env` git-ignored; gitleaks in CI; secret scrubbing in audit/log paths (engine); logs must not contain source. Gaps: secrets are plain environment variables in compose; production secret manager **not chosen**; no key rotation automation (manual runbook). `AUTH_MODE=dev` signs JWTs with one shared secret and must never be exposed publicly. MFA/SSO depends on the future OIDC provider **not chosen**.

### T-H7 Authentication and sessions
Dev mode is for local use only. OIDC mode: validate issuer, audience, signature (JWKS), expiry; reject `alg=none`. Rate limiting: per-IP (with a trusted-proxy count) and per-account exponential backoff exist, in-process only. Production refuses weak/placeholder `JWT_SECRET`; signup is closed by default in production; members join by hashed, expiring invitation. CSRF is limited because auth is a bearer header, not a cookie; if the web stores tokens, XSS impact must be reviewed **open**.

### T-H8 Availability and cost abuse
Per-run `max_usd`, entitlement check `can_start_run`, upload caps, pids/memory limits. Added: restrictive `free` default plan, per-user workspace and spend/run ceilings, atomic run admission with budget reservation, per-workspace estimate slots and SSE caps. Missing: fair scheduling (SCL-1), shared (multi-replica) limits, global spend alarms, a provider-side spend cap, e-mail verification (many accounts = many budgets).

### T-H9 Auditability (SEC-8)
`audit_log` table exists in the data model. Append-only enforcement: Postgres trigger and role revoke (migration 0004, **UNVERIFIED**); auth events are now audited (`/v1/me/audit`). Export **missing**. Deletion leaves a durable tombstone retried by the worker reaper and audited as `storage.purge`. Operator access to data is not itself audited today.

### T-H10 Supply chain and infrastructure
Dependencies scanned in CI (not yet run); base images unpinned by default (ARG for digest); `minio` image uses `latest` and must be pinned before use. Compose publishes ports on loopback only; TLS termination and a reverse proxy for production are **not built** (see NOTES).

## Prioritised actions
1. Cross-tenant negative tests on every route plus RLS under a non-owner role.
2. Run the Postgres test (`TEST_DATABASE_URL`) and finish RLS (memberships/audit_log/entitlements, composite FKs).
3. Per-run ephemeral worker without DB credentials.
4. Sign zero-retention terms with the model provider, or disclose plainly.
5. Production secret store and TLS ingress.
6. Independent penetration test before accepting real customer code.
