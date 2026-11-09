# Deployment guide

The supported deployment is a **run-to-completion container** (batch job or on-demand) that mounts the source
read-only and the project folder read-write. There is no long-running service; the review UI runs on a
reviewer's machine against the project folder.

## Image

`docker/Dockerfile` is multi-stage: a builder stage builds the wheel plus all dependency wheels (including the
`llm` extra); the runtime stage installs from those wheels offline into a slim Python 3.12 image.

* non-root user `10001:10001`; application code is root-owned (not writable by the agent);
* compatible with `--read-only`: `HOME`, caches and temp files point at `/tmp` (tmpfs); the only writable mounts are
  `/tmp`, `/scratch` (tmpfs) and `/project`;
* no secrets in the image, no `ENV` for keys; `pip --no-cache-dir`;
* OCI labels (`org.opencontainers.image.title|description|version|revision|created|source|base.name`);
* no `HEALTHCHECK`: it is a batch CLI, and `review` binds to loopback inside the container, so it is neither
  probeable nor reachable through a published port. Run `migration-agent review` on the host.

Build (context is the repository root):

```bash
VERSION=$(python3 -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
docker buildx imagetools inspect python:3.12-slim      # note the Digest
docker build -f docker/Dockerfile \
  --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> \
  --build-arg VERSION=$VERSION --build-arg VCS_REF=$(git rev-parse HEAD) --build-arg BUILD_DATE=$(date -u +%FT%TZ) \
  -t migration-agent:$VERSION .
```
Always pin the base by digest for anything you ship. Without the build arg the moving `python:3.12-slim` tag is used.

## Running: compose profiles

Both profiles need `REPO_DIR` (source, mounted `:ro` at `/workspace`) and `PROJECT_DIR` (project folder, mounted
read-write at `/project`; the container's working directory).

| Profile | Service | Network | Use |
|---|---|---|---|
| `offline` | `worker-offline` | `network_mode: none` | `init`, `ingest`, `extract`, `render`, `audit`, `changelog`, `analyze --offline` |
| `model` | `worker` + `egress-proxy` | internal network; only the Squid proxy has an exit, allowing HTTPS CONNECT to one host | `analyze`, `verify`, `resolve` |

```bash
export REPO_DIR=$PWD/legacy-orders PROJECT_DIR=$PWD/orders.migration
C="docker compose -f docker/docker-compose.yml"
# The source path is recorded in project.json, so run init inside the container with the in-container path:
$C --profile offline run --rm worker-offline init /workspace --project /project --name orders
$C --profile offline run --rm worker-offline ingest /project
$C --profile offline run --rm worker-offline extract /project
export ANTHROPIC_API_KEY=...            # or GEMINI_API_KEY; passed by name, never baked
$C --profile model run --rm worker analyze /project --max-usd 5
$C --profile model run --rm worker verify /project
```
Single-container alternative: `PROJECT_DIR=... docker/run.sh <repo-dir> analyze /project --offline` (network `none`
unless `MODEL_PROXY_NETWORK` names an internal network that you wired to a proxy yourself).

Hardening applied by compose/run.sh: `read_only`, `cap_drop: ALL`, `no-new-privileges`, uid 10001, 256 pids, 2 GB
memory, noexec tmpfs. Remember `/project` is writable by design, so mount only that folder.

### Choosing the model endpoint

Set `MODEL_ENDPOINT_HOST` to the **hostname** of the endpoint (no scheme, no port); the proxy renders its
allowlist from `docker/squid.conf` at start-up. Examples:

| Provider | `MODEL_ENDPOINT_HOST` | Other variables |
|---|---|---|
| Anthropic (default) | `api.anthropic.com` | `ANTHROPIC_API_KEY` |
| Gemini | `generativelanguage.googleapis.com` | `GEMINI_API_KEY`, `MA_LLM_CONFIG=/project/gemini.config.json` (copy the file into the project folder or mount it) |
| Corporate gateway / Azure / Bedrock proxy | gateway host | `MA_LLM_API_BASE=https://llm-gateway.corp.example/v1`, the key your gateway expects, `MA_LLM_MODEL*` |

## Environment variables

Read by `migration_agent.config_env` (precedence: defaults < `MA_LLM_CONFIG` file < `MA_*` variables). Empty values
are ignored; invalid values fail fast with a message naming the variable. Explicit CLI flags take precedence once the
CLI wires them (see the 0.2.0 changelog for what is wired).

| Variable | Meaning |
|---|---|
| `MA_LLM_CONFIG` | Path to a base `config.json` (same format as `--config`) |
| `MA_LLM_MODEL` | Default model for tasks without a specific override |
| `MA_LLM_MODEL_CHUNK_EXTRACT`, `_CROSS_REF`, `_BUSINESS_RULES`, `_GAP_FILL`, `_VERIFIER` | Model per task tier (any `MA_LLM_MODEL_<TASK>` sets task `<task>` in lower case) |
| `MA_LLM_API_BASE` | Custom endpoint URL (proxy, Azure, Bedrock gateway). Must match `MODEL_ENDPOINT_HOST` when using the model profile |
| `MA_LLM_PRICE_DEFAULT` | `input,output` USD per 1M tokens for models without a pricing entry, e.g. `0.3,2.5` |
| `MA_LLM_MAX_RETRIES`, `MA_LLM_BACKOFF_BASE`, `MA_LLM_BACKOFF_MAX` | Retry policy for 429/5xx/timeouts |
| `MA_LLM_TIMEOUT`, `MA_LLM_MAX_OUTPUT_TOKENS` | Per-call limits |
| `MA_MAX_USD` | Hard budget, same as `--max-usd` |
| `MA_LOG_LEVEL` | `DEBUG|INFO|WARNING|ERROR|CRITICAL` (default `WARNING`) |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY` | Provider credentials (read by LiteLLM). Inject with `--env-file`, Docker/Kubernetes secrets or your CI secret store, never `ENV` or build args |
| `MODEL_ENDPOINT_HOST`, `REPO_DIR`, `PROJECT_DIR`, `MA_IMAGE` | Compose-level: proxy allowlist host, mounts, image to run |

Note: `MA_LLM_API_BASE` is exposed by `config_env.load_settings_from_env()`; it takes effect in the LLM client once the
client applies it (tracked in the changelog).

## Releases, signing and verification

Version is defined once, in `pyproject.toml`; `migration_agent.__version__` reads it from package metadata.
To release:

```bash
git tag v0.2.0                                   # must equal pyproject version
python scripts/release.py v0.2.0                 # clean tree, tag format, build, dist/SHA256SUMS, prints cosign commands
git push origin v0.2.0                           # CI builds, pushes ghcr.io/<owner>/migration-agent:0.2.0, signs (keyless cosign), attaches SBOM
```
Verify as a consumer:
```bash
cd dist && sha256sum -c SHA256SUMS
cosign verify-blob --bundle migration_agent-0.2.0-py3-none-any.whl.sigstore.json \
  --certificate-identity-regexp 'https://github.com/<owner>/migration-agent/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com migration_agent-0.2.0-py3-none-any.whl
cosign verify ghcr.io/<owner>/migration-agent:0.2.0 \
  --certificate-identity-regexp 'https://github.com/<owner>/migration-agent/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
`scripts/release.py` never signs by itself. Signing in CI is keyless (GitHub OIDC; no stored secrets) and is skipped on
non-tag builds; set the repository variable `PYTHON_IMAGE` to the digest-pinned base for release builds.

## Upgrading

1. Read `CHANGELOG.md`. 2. Pull/build the new image. 3. Back up the project folder (below). 4. Run the same commands:
project files carry `schema_version`; the registry keeps IDs stable, and the new graph snapshot is a new version, so old
snapshots remain for diffing. 5. `llm_cache/` is keyed by prompt text and prompt version, so prompt changes naturally
miss the cache and cost tokens: run `analyze --dry-run` first.
Rollback: run the previous image against a restored backup.

## Backups and data handling

The project folder is the only state. Back it up with the tool you already use (`rsync -a`, snapshots); stop running jobs first
or copy `graph/` last. Exclude `llm_cache/` and `code/` from shared backups if the source is confidential (both are
re-creatable; `code/` has secrets redacted). `audit/audit.jsonl` is append-only evidence: keep it with the project.
Delete the folder to remove all derived data.

## Operational checklist

* Pin the base image by digest; verify the image signature before running it.
* Run with the compose hardening settings (read-only rootfs, no caps, non-root).
* Use `--max-usd` / `MA_MAX_USD` on every model run; review `audit` totals.
* Prefer the `offline` profile for everything that does not need a model.
* Treat the Squid proxy as a trusted component; allow only one host. Allowlisting is by domain name (see the
  caveats in `src/migration_agent/sandbox/README.md`).
* The Docker files have been reviewed but not exercised on the authoring machine (no Docker available); run a smoke test
  (`docker run --rm --read-only --network none --tmpfs /tmp migration-agent:<v> --help`) in your environment. CI does this on every build.
