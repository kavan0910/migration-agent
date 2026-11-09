# migration-agent documentation

| Document | Audience | Contents |
|---|---|---|
| [user-guide.md](user-guide.md) | Analysts, engineers running the tool | Install, project folder, every CLI command, LLM config, the review loop, security model, troubleshooting |
| [architecture.md](architecture.md) | Contributors, security reviewers | Component diagram, data flow, trust boundaries, design decisions |
| [api-reference.md](api-reference.md) | Integrators, contributors | Graph schema, findings/resolutions/comments formats, audit log, review-server HTTP API, Python entry points |
| [deployment.md](deployment.md) | Operators | Docker image, compose profiles, environment variables, model endpoints, upgrades, backups, releases |

These files are published alongside the tool: they are included in the source distribution (`sdist`) and live
in the repository under `docs/`. Note that each *analysis project* also has its own `docs/` folder
(`<name>.migration/docs/`) holding generated output; that is unrelated to this directory.
