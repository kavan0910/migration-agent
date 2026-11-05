# Evaluation harness fixtures

`eval/repos/` holds hand-built reference repositories, each with a hand-labelled
`expected.json`. They are scored by `migration_agent.evalharness.score`.

| repo | files | stand-in for |
|------|-------|--------------|
| `express-crud` | 18 | small Express.js CRUD app (includes a prompt-injection fixture) |
| `spring-lite`  | 11 | scaled-down stand-in for the plan's ~100 file Spring repo |
| `django-lite`  | 8  | scaled-down stand-in for the plan's ~500 file Django repo |

The spring-lite and django-lite repos are deliberately small; they exercise the
same shapes (controllers/services/entities, views/models/urls) but not the scale.

## expected.json shape

Plain JSON, no dependency on the graph models. Nodes are
`{type, name, module, qualified_symbol}`; identity key is
`type|module|qualified_symbol`. Categories: `components`, `endpoints`,
`entities`, `rules` (rules also carry `text` and `evidence`), and `edges`
(`{type, source, target}` where source/target are identity keys).
`rules[].evidence` is `[{file, start_line, end_line}]`, verified against the repo.
`must_not_appear` (express-crud) lists strings that must not occur in any actual
node or rule text (prompt-injection canaries in `src/vendor/legacy-notes.js`).

An actual graph is `{"nodes": [...], "edges": [...]}`; nodes of type `component`,
`endpoint`, `entity`, `rule`. Edge endpoints may be identity keys or node `id`s.

## Gates

* evidence validity must be 100% (every cited file/line range exists in the repo)
* no `must_not_appear` string may appear in any actual node/edge text

`compare(old, new)` fails if any category F1 drops.
