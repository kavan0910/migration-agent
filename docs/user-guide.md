# User guide

migration-agent reads a legacy service's source code and produces an **evidence-backed understanding document**
(`docs/understanding.latest.md`) plus a **knowledge graph** (components, endpoints, data entities, business rules,
...). Every claim cites file and line range with a verbatim quote. A **blind Verifier** then compares the document
with the documentation your team already has and reports contradictions, so humans can resolve them.

The tool never executes the code it analyses and never modifies the source.

## 1. Install

Requires Python 3.11 or newer.

```bash
python -m venv .venv && . .venv/bin/activate
pip install ".[llm]"          # from a checkout; "[llm]" adds LiteLLM, needed for real model calls
migration-agent --help
```

Without the `llm` extra everything works except real model calls (`analyze --offline`, `extract`, `render`,
`ingest`, `audit`, `changelog` and `review` need no model). Add `pytest` with `pip install -e ".[dev,llm]"` for development.
For containers see [deployment.md](deployment.md).

### API keys and `.env`

Model access is configured through environment variables understood by LiteLLM:
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `OPENAI_API_KEY`. The CLI refuses to start a real
analysis if none of them is set.

On start-up the CLI loads a `.env` file (copy `.env.example`): from the checkout root (the directory containing
`pyproject.toml`) or, when installed elsewhere, from the current directory. Rules: `KEY=VALUE` lines, optional `export `, quotes
stripped, `#` comments; **variables already in the environment are never overridden**. `.env` is git-ignored;
never commit it.

## 2. The project folder

All state lives in one folder, `<source-name>.migration/` by default. It is safe to copy, back up or commit
(except `llm_cache/`, see below).

```
svc.migration/
  project.json          name, source, created, schema_version
  code/                 normalised copy of the service (secrets redacted)   <- ingest
  ingest_report.json    what was copied/skipped, secret findings (no values) <- ingest
  facts/                ast.json, manifests.json, config.json               <- extract
  inventory.json        coverage inventory (INV-nnnnn items)                <- extract
  graph/                snapshots/vNNN.json, latest.json, registry.json     <- analyze/resolve
  checkpoint/           per-chunk analysis patches (lets analyze resume)
  llm_cache/            cached model responses keyed by prompt hash
  docs/                 understanding.latest.md, graph.html, changelog.md
  user_docs/            YOUR existing docs (.md .txt .html .docx .pdf), for the Verifier only
  feedback/             findings.json, resolutions.json, comments.json, reanalysis_log.json
  audit/                audit.jsonl (append-only)
```

`graph/registry.json` keeps IDs (`COMP-0001`, `RULE-0003`...) stable across re-analyses so references in your
reviews and docs do not break. `llm_cache/` contains prompts' responses derived from your code: treat it as
sensitive and exclude it from sharing.

## 3. Quick start

```bash
migration-agent init ./legacy-orders                 # creates ./legacy-orders.migration
migration-agent ingest legacy-orders.migration
migration-agent extract legacy-orders.migration
cp requirements.md legacy-orders.migration/user_docs/   # optional: docs for the Verifier
migration-agent analyze legacy-orders.migration --dry-run          # plan + cost estimate
migration-agent analyze legacy-orders.migration --max-usd 5
migration-agent verify  legacy-orders.migration
migration-agent review  legacy-orders.migration      # opens the local review UI
```

Try the pipeline without any model: `migration-agent analyze PROJECT --offline` (deterministic stand-in; produces
the facts-level graph and documents, not a real analysis).

## 4. Commands

All commands take the project folder as the first argument. Every command appends to `audit/audit.jsonl`.
Run `migration-agent COMMAND --help` for the authoritative option list.

### `init SOURCE [--project PATH] [--name TEXT]`
Creates the project folder. `SOURCE` is a directory, `.zip`, tarball or git URL. Nothing is copied yet.
```bash
migration-agent init https://example.com/team/orders.git --project ./orders.migration --name "Orders service"
```

### `ingest PROJECT`
Copies the source into `code/`. Safe by construction: archive entries with absolute paths, `..`, NUL bytes are
rejected; size limits (500 MB, 200,000 files, compression ratio 100) apply; git clones run with hooks disabled;
symlinks, binaries, files over 1 MB and vendor/build directories (`.git`, `node_modules`, `vendor`, `build`,
`dist`, `target`, virtualenvs) are skipped. Credential files (`.env`, `*.pem`, `id_rsa`, `.npmrc`, ...) are
**withheld entirely**; secrets found in other files are replaced with `[REDACTED:<kind>]` in `code/`. Writes
`ingest_report.json`.

### `extract PROJECT`
Static extraction with tree-sitter: AST facts, dependency manifests, configuration keys, routes, import
edges. Writes `facts/` and `inventory.json`, the list of everything the analysis must account for.
Prints inventory counts by kind (source_file, route, config_key, dependency).

### `analyze PROJECT [--offline] [--dry-run] [--config PATH] [--max-usd FLOAT]`
Runs (and, if needed, first runs `extract`) the full pipeline: chunk plan, per-chunk model extraction, merge,
cross-reference resolution, evidence verification, business-rules pass, gap fill for unmapped inventory items,
then renders `docs/understanding.latest.md` and `docs/graph.html` and saves a new graph snapshot.

| Option | Meaning |
|---|---|
| `--dry-run` | Print `plan: N chunk(s), estimated cost $X` and exit. No key needed. |
| `--max-usd 5` | Hard budget. The run **stops gracefully** and keeps what it has; re-run to continue (cache and checkpoints avoid paying twice). |
| `--config FILE` | LLM configuration (section 5). Default: built-in Anthropic tiers. |
| `--offline` | No model; deterministic stand-in. |

```bash
migration-agent analyze orders.migration --config gemini-lite.config.json --dry-run
migration-agent analyze orders.migration --config gemini.config.json --max-usd 3
```

### `render PROJECT`
Builds a **facts-only** graph from the inventory (no model) and renders the document. Useful to check
extraction. Note it creates a new snapshot from facts only; run `analyze` for the real result.

### `verify PROJECT [--config PATH] [--max-usd FLOAT]`
The blind Verifier compares `docs/understanding.latest.md` with the files in `user_docs/` and writes
`feedback/findings.json`. Requires an existing understanding doc. It sees only those two inputs, never the code.
Each finding quotes both sides verbatim; findings whose quotes cannot be string-matched in the source text are
dropped (`dropped_unverifiable`). Kinds: `contradiction`, `doc_only` (documented but not in code), `code_only`
(in code but undocumented), `doc_vs_doc`. Prints one line per finding.

### `review PROJECT [--port N] [--no-browser]`
Starts the local review UI (section 6). Prints a URL containing a one-time token.

### `resolve PROJECT [FINDING_ID] [--status S] [--note TEXT] [--comment ITEM_ID] [--reanalyze/--no-reanalyze] [--config PATH] [--max-usd FLOAT]`
Records a decision, then runs **targeted re-analysis** of only the affected nodes and regenerates the changelog.
Statuses: `accepted_doc` (the document is right, the code differs: re-analyse), `accepted_code` (code is the truth),
`dismissed`, `needs_reanalysis`.
```bash
migration-agent resolve orders.migration F-0001 --status accepted_doc --note "Discount is 25% from 10 items"
migration-agent resolve orders.migration --comment RULE-0003 --note "Check the VIP exception in pricing.py"
migration-agent resolve orders.migration F-0004 --status dismissed --no-reanalyze
```
Your note is passed to the model as untrusted *data*; every resulting claim still needs verbatim code evidence.
If the budget stops a re-analysis, nothing is written.

### `changelog PROJECT`
Regenerates `docs/changelog.md` from consecutive graph snapshots, annotated with the comment or resolution that
caused each change.

### `audit PROJECT [--format table|json]`
Prints the audit log with totals (calls, tokens, USD).
```bash
migration-agent audit orders.migration --format json | jq '.[] | select(.status=="error")'
```

## 5. LLM configuration

`--config FILE` takes a JSON file; the settings may sit under a top-level `"llm"` key. Two ready-made files
ship in the repository root:

* `gemini.config.json`: all tasks use `gemini/gemini-3.5-flash`.
* `gemini-lite.config.json`: all tasks use `gemini/gemini-3.1-flash-lite` (cheapest, lowest quotas).

Fields (`LLMConfig`):

| Field | Default | Meaning |
|---|---|---|
| `models` | haiku for `chunk_extract`, sonnet for the rest | model per task tier: `chunk_extract`, `cross_ref`, `business_rules`, `gap_fill`, `verifier` |
| `default_model` | `claude-sonnet-5-5` | used for tasks missing from `models` |
| `pricing` / `default_pricing` | per-model table / `[3.0, 15.0]` | USD per 1M tokens `[input, output]`, used for budget and estimates. Set it for any non-default model, else costs are mis-estimated |
| `max_retries` | 4 | retries on 429 / 5xx / timeouts, exponential backoff with jitter |
| `backoff_base`, `backoff_max` | 1.0, 30.0 s | |
| `timeout` | 120 s | per call |
| `max_output_tokens` | 4096 | |

**Anthropic** (default; just set `ANTHROPIC_API_KEY`): cheap tier for bulk chunk extraction, stronger model for
reasoning tasks:
```json
{"llm": {"models": {"chunk_extract": "claude-haiku-5-5", "cross_ref": "claude-sonnet-5-5",
                    "business_rules": "claude-sonnet-5-5", "gap_fill": "claude-sonnet-5-5",
                    "verifier": "claude-sonnet-5-5"},
         "pricing": {"claude-haiku-5-5": [1.0, 5.0], "claude-sonnet-5-5": [3.0, 15.0]}}}
```
**Gemini**: set `GEMINI_API_KEY` and use `--config gemini.config.json`. Those files raise `max_retries` to 8
and backoff to 5 to 90 s because free-tier limits are tight.

Equivalent environment-variable configuration for containers (`MA_LLM_MODEL_<TASK>`, `MA_LLM_API_BASE`, ...) is
described in [deployment.md](deployment.md). Responses are cached in `llm_cache/`; identical re-runs cost nothing.

## 6. The review loop and server

1. `verify` produces findings. 2. `review` shows the document (with coverage and confidence), findings, graph
versions and diffs, and lets you comment on any graph item and resolve findings. 3. `resolve` (or the UI)
triggers targeted re-analysis -> new graph version -> `docs/changelog.md`.

Review server security: it binds **127.0.0.1 only** (refuses anything else), picks a random port unless
`--port` is given, and every request needs an unguessable token (URL path or `HttpOnly; SameSite=Strict`
cookie, constant-time compare). It validates the `Host` header (DNS-rebinding defence), rejects cross-origin POSTs,
requires `application/json`, caps bodies at 64 KB and texts at 5,000 characters, and exposes only comments and
resolutions for writing. Do not forward the port. It cannot be used from inside a container; run it on the host.

## 7. Security model

* **Read-only on the source.** `ingest` copies; analysis reads only `code/`. Nothing from the source is executed.
* **Tool sandbox.** The model only gets `read_file`, `list_dir`, `grep`, `file_stat`, jailed to `code/` (symlinks resolved,
  `..` and NUL rejected, size caps, no write/exec/network capability exists).
* **Process guard.** An optional audit-hook tripwire blocks writes outside scratch space, process spawn and
  non-allowlisted sockets. It is defence in depth, not a boundary (see `src/migration_agent/sandbox/README.md`).
* **Container boundary.** The Docker image runs non-root with a read-only root filesystem, all capabilities
  dropped, and either no network or egress only through an allowlisting proxy to the model host.
* **Secrets.** Credential files are withheld at ingest and secret values are redacted from `code/`, reports, and user docs before they reach a prompt or `findings.json`.
  Findings contain kinds and redacted previews, never values. API keys are read from the environment and never written to the project folder or image.
* **Untrusted text.** Source code, user docs and reviewer notes are wrapped in delimiters that they cannot forge and
  are treated as data. A hand-built fixture in `eval/` checks prompt-injection canaries do not reach the graph.
* **User docs only to the Verifier.** The analysis never reads `user_docs/`, so the doc is derived from code alone; the Verifier
  never sees code. That separation is what makes disagreements meaningful.
* **Evidence.** A node without verifiable file/line/quote evidence has confidence `none`/`low` and is flagged in
  the document's blind-spots section.
* **Data leaves your machine only to the configured model endpoint.** Chunks of code go to that provider; use
  `--offline`, a self-hosted gateway (`MA_LLM_API_BASE`) or a no-retention provider plan if that matters.

## 8. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `No LLM API key found` | Export the key or put it in `.env` (section 1); or use `--offline`/`--dry-run`. |
| macOS `Operation not permitted` | The terminal lacks access to protected folders (Downloads, Desktop, Documents, external volumes). Grant the terminal app access in System Settings -> Privacy & Security (Files and Folders, or Full Disk Access), or keep the project and source elsewhere, e.g. `~/work`. Also appears when `.env` or the source lives in such a folder. |
| `429` / quota / `RESOURCE_EXHAUSTED` | Free tiers have per-minute and per-day limits that differ by model (lite models usually have the highest limits; larger "flash" models the lowest). The client retries with backoff up to `max_retries`; for free tiers use `gemini-lite.config.json`, raise `backoff_max`, or just re-run later: cache and checkpoints mean finished chunks are not repeated. |
| `503` / "model is overloaded" | Transient provider overload; retried automatically (5xx). If it persists after `max_retries`, the chunk is skipped (named in the log) and a re-run picks it up. Raise `MA_LLM_MAX_RETRIES` or switch model. |
| Run stopped on budget | `--max-usd` reached. Raise it and re-run; completed work is reused. |
| Many `unmapped` inventory items | Check `docs/understanding.latest.md` section 15 and `ingest_report.json` exclusions (large/binary/vendor files). |
| Low confidence / "blind spots" | Evidence quotes did not verify against `code/`; re-run, use a stronger `models.*` for that task, or comment on the item and `resolve`. |
| `not a project folder` | The folder lacks `project.json`; run `init` first. |
| `verify` reports nothing | `user_docs/` is empty or the formats are unsupported (`.pdf` needs `pypdf`, which is not installed by default). |
| `review` cannot be reached from a container | By design it binds to loopback; run it on the host. |
