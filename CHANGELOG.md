# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).
The version lives in `pyproject.toml` only.

## [0.2.0] - unreleased

### Added
- Production Docker image (`docker/Dockerfile`): multi-stage, digest-pinnable base, non-root, read-only rootfs,
  OCI labels, project folder mount at `/project`.
- `MA_*` environment configuration (`migration_agent.config_env`): per-task models, custom endpoint, retries, budget, log level.
- Compose: `MA_*` pass-through, configurable model endpoint host for the egress proxy, `PROJECT_DIR` mount.
- Release tooling (`scripts/release.py`), CI workflow (tests, wheel check, image build, keyless cosign signing and SBOM on tags).
- Documentation: user guide, architecture, API reference, deployment guide.
- `migration_agent.__version__` from package metadata.

### Changed
- Package metadata (readme, classifiers); sdist now includes docs and docker files.

### Known gaps
- `migration-agent --version`, applying `config_env` in the CLI, and honouring `MA_LLM_API_BASE` in the LLM client
  are not yet wired (owned by `cli.py` / `llm/`).
- Docker files were reviewed but not built on the authoring machine.

## [0.1.0]
- Phases A to C: ingest, extraction, analysis pipeline, Verifier, review server, resolutions, changelog.
