# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).
The version lives in `pyproject.toml` only.

## [Unreleased] - 0.3.0 platform

### Added
- Hosted platform scaffolding (see `docs/platform-contract.md`): `deploy/` compose stack (postgres, API, hardened worker behind an allowlisting egress proxy, web, optional MinIO), `Dockerfile.backend`, `Dockerfile.web`, `.env.example`, `smoke.sh`.
- CI workflows for backend, web and security (pip-audit, npm audit, Trivy, gitleaks, compose validation). Not yet verified: repository Actions runs currently end in `startup_failure`.
- `docs/security/`: data handling and retention draft, subprocessor template, incident response outline, secure SDLC, hosted threat model, SOC 2 readiness checklist, customer security overview draft; `SECURITY.md`.
- `docs/operations-runbook.md`.

### Known gaps
- No certification or audit of any kind. Stack and images not yet built or run end to end; no TLS ingress; worker is long-lived and holds database credentials; no billing by design.

## [0.2.0] - 2026-11-09

### Added
- Production Docker image (`docker/Dockerfile`): multi-stage, digest-pinnable base, non-root, read-only rootfs,
  OCI labels, project folder mount at `/project`.
- `MA_*` environment configuration (`migration_agent.config_env`): per-task models, custom endpoint, retries, budget, log level.
- Compose: `MA_*` pass-through, configurable model endpoint host for the egress proxy, `PROJECT_DIR` mount.
- Release tooling (`scripts/release.py`), CI workflow (tests, wheel check, image build, keyless cosign signing and SBOM on tags).
- Documentation: user guide, architecture, API reference, deployment guide.
- `migration_agent.__version__` from package metadata.
- `migration-agent --version`, `MA_*` config applied in the CLI, and `MA_LLM_API_BASE` honoured by the LLM client.
- Framework/schema/spec extractors, incremental runs and error recovery; review patches persist across analyze; stable edge IDs.

### Changed
- Package metadata (readme, classifiers); sdist now includes docs and docker files.

### Known gaps
- Image is built and smoke-tested locally (read-only rootfs, no network) but not yet digest-pinned, scanned or signed; CI does that on tag.

## [0.1.0]
- Phases A to C: ingest, extraction, analysis pipeline, Verifier, review server, resolutions, changelog.
