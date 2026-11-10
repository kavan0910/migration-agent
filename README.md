# migration-agent

Read-only analysis of legacy services. It ingests a codebase, extracts static facts, uses an LLM to build an
**evidence-backed knowledge graph and understanding document** (every claim cites file, lines and a verbatim quote),
then runs a **blind Verifier** that compares the document with your existing documentation and feeds a human
review loop with targeted re-analysis and a changelog.

* Never executes or modifies the analysed code; model access is limited to path-jailed read-only tools.
* Secrets are withheld/redacted at ingest. Optional hardened Docker image with no network or model-only egress.
* One project folder holds all state; cost is bounded with `--max-usd` and cached/resumable.

## Quick start

```bash
pip install ".[llm]"
cp .env.example .env            # add ANTHROPIC_API_KEY or GEMINI_API_KEY
migration-agent init ./my-service
migration-agent ingest  my-service.migration
migration-agent extract my-service.migration
migration-agent analyze my-service.migration --dry-run         # plan + cost estimate
migration-agent analyze my-service.migration --max-usd 5
migration-agent verify  my-service.migration                   # needs docs in my-service.migration/user_docs/
migration-agent review  my-service.migration
```
No key yet? `migration-agent analyze my-service.migration --offline` runs the pipeline with a stand-in model.

## Hosted platform (in development, 0.3.0)

Beyond the CLI, the repo is growing a multi-tenant web product: a FastAPI backend and worker, a Next.js UI and a
docker-compose stack with a hardened, egress-restricted worker. Nothing here is a released or audited service, and there
is no billing. Design: [docs/product-architecture.md](docs/product-architecture.md); contract between the parts:
[docs/platform-contract.md](docs/platform-contract.md).

Repo map:

| Path | What |
|---|---|
| `src/migration_agent/` | Engine and CLI (pip-installable) |
| `backend/` | API and worker (FastAPI) |
| `web/` | Web UI (Next.js) |
| `deploy/` | Compose stack, Dockerfiles, `.env.example`, `smoke.sh` |
| `docker/` | Hardened single-run engine image and compose profiles |
| `docs/security/`, `SECURITY.md` | Data handling, threat model, SOC 2 readiness (not certified), disclosure policy |
| `docs/operations-runbook.md` | Deploy, backup/restore, rotation, on-call |

Run the stack (requires Docker and the backend and web directories):

```bash
cp deploy/.env.example deploy/.env      # set POSTGRES_PASSWORD, JWT_SECRET and a model key
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
deploy/smoke.sh                         # health, signup, sample project, key endpoints
```
Web on http://localhost:3000, API on http://localhost:8000. See the [runbook](docs/operations-runbook.md).

## Documentation

[docs/](docs/README.md): [user guide](docs/user-guide.md), [architecture](docs/architecture.md),
[API reference](docs/api-reference.md), [deployment guide](docs/deployment.md). Release history: [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,llm]"
PYTHONPATH=src python -m pytest -q
```
Docker: see `docker/` and the deployment guide. Releases: `python scripts/release.py vX.Y.Z`.
