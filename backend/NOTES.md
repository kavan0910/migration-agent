# Backend notes (stream B)

## Built

* All endpoints of contract section 3, data model of section 4 (plus `comments`, `resolutions`), worker of section 5,
  billing seam of section 6. Alembic migrations (0001 schema, 0002 Postgres RLS); tested on SQLite only.
* Auth: `AUTH_MODE=dev` (argon2 password hashes, short-lived HS256 JWT via PyJWT) and `oidc` (PyJWT + `PyJWKClient`,
  asymmetric algorithms only; users created on first sight; unverified emails are never linked to existing accounts).
  Behind `TokenVerifier`; no hand-rolled crypto.
* Tenancy: `app/tenancy.py::scoped_select` joins membership for the caller; `workspace_ctx`, `project_ctx`, `run_ctx` are
  the only way routes reach tenant data. Tests hit every endpoint as a foreign user and as anonymous.
* Ingestion safety: https-only git URLs with DNS resolution and public-address check (all A/AAAA), numeric-host
  spellings rejected, port 443 only, no credentials, optional host allowlist; hardened clone (hooks off, redirects off,
  protocol allowlist, DNS pinning via `http.curloptResolve`, timeout, size watch, `.git` and symlinks removed). Archives:
  zip-slip, absolute/backslash/drive paths, links and special files, encrypted zips, duplicate names, file-count, per-file
  and total size counted from the real stream (headers are not trusted), compression-ratio cap. Request-body caps in an
  ASGI middleware (413).
* Runs: queue with `SKIP LOCKED`/atomic claim, per-project execution lock (CAS on the project row), heartbeat + stale
  reaper, retries with exponential backoff and jitter for transient errors and crashes, per-run idempotency through
  `completed_stages` plus state upload after every stage, cancel between stages, budget = min(run max_usd - spent,
  workspace monthly remaining), unattributed engine cost is metered, scratch dir always removed, project deleted mid-run
  aborts and wipes anything the worker uploaded.
* Observability: request id middleware, JSON logs without bodies/headers/query strings/source; run events scrub the API key.
* Deletion: rows and objects removed, audit record written in the same transaction; `usage_events` kept but detached.

## Deliberately not built

* Any payment code, SDK, keys, webhooks or prices. Plan limits in `DEFAULT_PLANS` are resource ceilings, not prices.
* GitHub App / private repos (only public https clone). Email verification, password reset, MFA (use the OIDC mode).
* Shared (multi-replica) rate limiting; the token bucket is per process.
* Mid-stage cancellation of the engine: cancel/deletion is honoured between stages (engine has no cancel hook).
  The engine's own `max_usd` enforcement is the in-stage budget stop.
* Postgres-specific code paths (`FOR UPDATE SKIP LOCKED`, RLS policies, JSONB) are written but NOT executed by the tests.
* S3Store is a skeleton (unit-tested with a fake client only).

## Deviations from the contract

* `POST .../estimate` runs ingest+extract+dry-run **inside the API process** in a scratch dir (bounded to 2 concurrent),
  because the response is synchronous. It therefore reads customer code in the API tier, and for an un-fetched git source
  it clones from the API host. If the control plane must never touch code, move estimate to a worker job (async).
* Extra status codes: 413 (payload too large), 409 `no_source`/`no_analysis`/`run_in_progress`, 403 `entitlement_denied`.
* Graph diff is computed from stored snapshots by id (same shape as the review UI diff); node `q` search is a substring
  match over the node JSON.
* `needs_reanalysis`/`accepted_doc` resolutions enqueue a `reanalyze` run through `can_start_run`; if denied the
  resolution is still recorded and `run_denied_reason` says why. Concurrent triggers coalesce into the queued run.
* The LLM cache lives inside each project's state archive (per project, therefore per workspace and removed with the
  project) instead of a shared per-workspace cache.
* Run `analyze` = ingest (skipped when the stored state already contains the same source) + extract + analyze.
  `verify` and `reanalyze` need a previous analysis. User docs are copied to `user_docs/` for the Verifier only.

## Open problems

* RLS: policies exist but are ENABLED, not FORCED, so they protect only a non-owner DB role; needs a Postgres test run.
* Comment text is stored raw in the DB (the engine redacts secrets when it ingests the comment, the API copy is not).
* DNS pinning for git relies on libcurl honouring `http.curloptResolve` (git >= 2.37); also enforce with the egress proxy.
* Results are read whole from the object store per request (no cache); very large graphs need server-side indexing
  (product-architecture PERF-1).
* A worker killed mid-stage re-runs that stage after the reaper timeout (state from the previous stage is reused).
* `ENGINE_OFFLINE` exists for demos and tests; make sure it is never set in production.
