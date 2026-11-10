# Engine service facade (stream A)

## Built
- `service.py`: contract section 2 API (`run_stage`, `estimate`, `review_state`, `graph_snapshot`, `graph_versions`,
  `graph_diff`, `add_comment`, `add_resolution`, `document`, `changelog`, `pack_project`, `unpack_project`),
  plus `StageResult`, `ServiceError` (`.code` = `not_found` | `invalid`), `Event`, `STAGES`.
- `storage.py`: deterministic tar.gz pack and hardened unpack (`ArchiveError`). Caps: 1 GiB archive, 4 GiB unpacked,
  250k entries (keyword-overridable). Rejects absolute paths, `..`, backslashes, NUL, drive letters, symlinks, hardlinks,
  devices, FIFOs, duplicates; streaming extraction (bomb cut off at the cap); O_EXCL|O_NOFOLLOW writes; destination
  must be new or empty and is restored on failure. Pack skips symlinks and special files; `include_cache=False` drops
  top-level `llm_cache/`.
- Shared code extracted, CLI now calls it: `pipeline.run_ingest_stage`, `run_extract_stage`, `ensure_extracted`.
- `analyze.runner.run_analyze(..., on_progress=cb)`: per-chunk `(done, total)` callback (reused chunks counted at start).
- `LLMConfig.api_key` (excluded from dumps and repr): passed to the provider call when set. The service never calls
  `load_llm_config_from_env`; config comes only from the dict.
- Review logic is reused from `review_ui.server.ReviewState` (already HTTP-independent), one instance + lock per project dir
  per process, so service output is exactly what `/api/state` serves.
- Events: `usage` per LLM call (any client, via `_EventLLM`), `progress` per chunk (analyze) or 1/1 (ingest/extract),
  `log` messages. Callback is serialized by a lock, exceptions in it are swallowed and logged. The run's api_key and
  detected secrets are scrubbed from log events and `StageResult.error`.
- Budget: `max_usd` (or `config["max_usd"]`) -> client Budget (pre-call estimate) + metering wrapper (post-call) ->
  `status="stopped_budget"`, partial results still rendered. `LLMAuthError` -> `status="failed"`.

## Deviations from the contract
- `run_stage` returns `failed` for every expected error (missing project, bad finding id, ingest into non-empty
  code/, provider errors) and only raises `ServiceError` for an unknown stage name.
- `resolve` records via `review.resolutions` (CLI behaviour: one resolution per finding, replaced), while the public
  `add_resolution` uses `ReviewState` (UI behaviour: appends). Both pre-existed and differ; not unified to keep both
  behaviours identical to before.
- `add_resolution(author=)` is accepted but ignored (Resolution has no author field).
- `reanalyze=False` by default (CLI default is True). `incremental=True` on `ingest` means "refresh code/".
- `unpack_project` has extra optional keyword caps.
- `graph_snapshot()` with no graph returns `{"version": 0, "nodes": [], "edges": []}`.

## Not built
- No `Storage` interface with object-store backends (E1): only the archive primitives; the backend owns upload/download.
- No cancellation token or wall-clock timeout inside the engine (a worker must kill the process/container).
- No per-stage progress for verify/resolve beyond usage events.
- Posix only (`fcntl` in `audit.py`).

## Open problems
- `llm_cache/` is keyed by prompt hash, not tenant: never share one cache between tenants (SEC-1). Use per-project
  caches (default) or `include_cache=False`.
- The budget check is estimate-based before a call and actual-cost after; one in-flight call per worker thread can
  overshoot `max_usd` slightly with `workers > 1`.
- `run_analyze` uses `_Metered` (private) and `estimate` for the pre-run warning only runs for real clients.
- File-lock based audit log and atomic writes assume the scratch dir is a local disk.
- `tests/e2e` leaves a replay stand-in for `LiteLLMClient` patched; tests in `test_service.py` that need the real client
  restore it via `_real_client`.
- `api_key` redaction in error text is exact-match plus the existing secret patterns (no generic `sk-ant-` pattern exists).
