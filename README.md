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

## Documentation

[docs/](docs/README.md): [user guide](docs/user-guide.md), [architecture](docs/architecture.md),
[API reference](docs/api-reference.md), [deployment guide](docs/deployment.md). Release history: [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev,llm]"
PYTHONPATH=src python -m pytest -q
```
Docker: see `docker/` and the deployment guide. Releases: `python scripts/release.py vX.Y.Z`.
