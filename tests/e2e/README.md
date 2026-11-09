# End-to-end suite

Drives the real CLI (`init -> ingest -> extract -> analyze -> verify -> resolve (reanalysis + comment) -> changelog`)
through click's `CliRunner` on the three reference repos in `eval/repos/`, with no network.

## How the LLM is faked

`conftest.py` replaces `migration_agent.llm.litellm_client.LiteLLMClient` with a factory that builds the **real**
client around `ReplayBackend.transport` (`e2e_replay.py`). Cost accounting, the disk cache, the budget guard and
the audit log therefore all run for real. The replay answers are derived from `eval/repos/<repo>/expected.json`
(node/edge/rule lists with evidence lines read from the actual repo files) plus canned Verifier findings
(one contradiction, one doc-only, three code-only, one fabricated quote, one invalid kind).
Each task tier uses its own model name `fake/<task>` so the transport can tell tasks apart; prices are
3 / 15 USD per M tokens (see `write_config`).

Runs are cached per session (`get_run`), so the whole suite takes well under a second or two.

## Files

| file | contract |
|------|----------|
| `test_pipeline_e2e.py` | every stage succeeds, artifacts exist, versions advance, review state consistent |
| `test_graph_schema.py` | Graph models validation, ID formats, ID stability, evidence validity, coverage, score floors + golden |
| `test_doc_structure.py` | section order, anchors exactly once, links resolve, coverage section |
| `test_findings_classification.py` | four kinds, severities, quote verification, dropped count, code-only grouping |
| `test_cost_tracking.py` | audit totals, cache = 0 cost, `--max-usd` partial results and resume, dry-run estimate in [0.5x, 2x] |

## Golden files (`golden/`)

| file | content | compare |
|------|---------|---------|
| `graph.schema.json` | `Graph.model_json_schema()` | exact |
| `doc.headings.json` | `#`/`##` heading skeleton of `understanding.latest.md` | exact |
| `<repo>.findings.json` | `(kind, severity)` list + dropped count | exact |
| `<repo>.score.json` | evalharness `Report` for the replay run | `evalharness.compare` (F1 may not drop by more than 0.05) |

Regenerate after an **intended** change (schema change, template change, extractor change):

```
UPDATE_GOLDEN=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/e2e
git diff tests/e2e/golden        # review what moved before committing
```

Without `UPDATE_GOLDEN`, a missing golden file fails with a hint instead of being created silently.

## Known bugs encoded as xfail (non-strict)

* `test_edge_ids_survive_review_cycle`: re-analysis re-allocates IDs for edges rewritten by `collapse_entity_components`.
  Remove the xfail marker when fixed.

## Live run (not part of pytest)

`scripts/e2e_live.py <config.json> [repo ...]` runs the same flow against a real model using `.env` keys and prints
evalharness scores and the audit cost. It is never collected by pytest and costs real money.
