# Secure development lifecycle

Status column is honest: "in place" means it exists in this repo today; "defined" means written but not yet enforced.

| Practice | Control | Status |
|---|---|---|
| Design | Threat model per surface (`docs/threat-model.md` engine, `threat-model-hosted.md` platform); security findings tracked as SEC-n in `docs/product-architecture.md` | In place (documents) |
| Code review | All changes via pull request, one reviewer other than the author | **Defined; not enforceable while solo.** Branch protection not yet configured |
| Tests | Engine 580+ tests, red-team fixtures, eval regression gate; backend/web tests in CI | Engine in place; platform tests depend on streams B and C |
| Cross-tenant tests | Tests that attempt reads across workspaces must exist and run in CI | Contract requires; **verify in backend** |
| Static analysis / lint | `ruff`, `mypy` (backend), ESLint + `tsc` (web) in CI | Workflows written; **not yet run** (see runbook note on Actions) |
| Dependency scanning | `pip-audit`, `npm audit`, Trivy fs in `.github/workflows/security.yml`, weekly schedule | Written; not yet run |
| Secret scanning | gitleaks over full history + Trivy secret scanner; `.env` git-ignored; placeholders only in examples | Written; not yet run. Enable GitHub push protection (setting, not code) |
| Image scanning | Trivy on built backend/web images | Written; not yet run |
| Supply chain | Base image pinning by digest for releases; keyless cosign signing and SBOM on tags (engine image) | Engine image in place; platform images not yet signed |
| Secrets management | Provider keys injected at runtime to the worker only; never in images, build args or project files; dev JWT secret is dev-only | In place by design; production secret store **not chosen** |
| Least privilege | Non-root, read-only rootfs, caps dropped, no-new-privileges, pids/memory limits; worker egress via allowlist proxy | In place in `deploy/docker-compose.yml`; **not yet exercised against a running stack** |
| Release | Version in `pyproject.toml`, `scripts/release.py`, CHANGELOG | In place for engine |
| Vulnerability handling | `SECURITY.md`; fix targets by severity | Defined; no triage history |
| Change of risk | Any change touching auth, tenancy, worker sandbox, storage or egress needs a threat-model note in the PR | Defined |
| Penetration test | Before first paying customer / design-partner with real code | **Not done** |

Definition of done for security-relevant changes: test that fails without the fix, threat-model/doc update, no new
unscanned dependency, no secret in diff.
