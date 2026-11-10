# SOC 2 readiness checklist

**This project is NOT SOC 2 compliant, has NOT been audited, and no report exists.** This checklist maps the AICPA
Trust Services Criteria (Security category first) to what exists and what is missing, to plan a future Type I
engagement. Per `docs/product-architecture.md`, start the formal path once there are customers. Legend: Exists =
implemented in this repo; Partial; Missing.

| Area (TSC ref) | Control | Evidence today | Status | Gap / next step |
|---|---|---|---|---|
| Control environment (CC1) | Security roles, policies approved by management | Drafts in `docs/security/` | Partial | Owner approval, signed policy set, training record |
| Communication (CC2) | Customer-facing commitments; internal reporting | `security-overview-draft.md`, `SECURITY.md` | Partial | Publish after owner review; status page missing |
| Risk assessment (CC3) | Documented risk process | `threat-model.md`, `threat-model-hosted.md`, product-architecture s8, s14 | Partial | Periodic risk register with owners and review dates |
| Monitoring (CC4) | Ongoing control monitoring, vulnerability scans | CI workflows written (`security.yml`) | Partial | Not yet running (Actions startup_failure); schedule reviews |
| Control activities (CC5) | Control selection and deployment | This checklist | Partial | Map every control to an owner |
| Logical access (CC6.1-6.3) | Auth, least privilege, role-based access | Roles in contract (owner/admin/member/viewer); OIDC mode; invitation-based membership; per-account login backoff; strong-secret enforcement in production; auth events audited | Partial | MFA/SSO needs IdP (not chosen); access reviews missing; operator access is unmanaged; RLS not verified on Postgres |
| Logical access (CC6.6-6.7) | Boundary protection, encryption in transit | Internal networks, egress allowlist proxy | Partial | TLS ingress not built; encryption in transit between services not configured |
| Encryption at rest (CC6.1) | DB and object store encryption | None in compose | Missing | Depends on hosting choice; verify provider default and key management |
| System operations (CC7.1-7.2) | Detection and monitoring | Structured logs (log-forging safe), audit log table (Postgres append-only trigger, UNVERIFIED) | Partial | No alerting, no log retention sink, no uptime check |
| Incident response (CC7.3-7.5) | IR plan, post-incident review | `incident-response.md` | Partial | Never exercised; no on-call tooling |
| Change management (CC8) | Reviewed, tested, authorised changes | PRs, CI tests, CHANGELOG, release script | Partial | No enforced review/branch protection; deploys are manual |
| Risk mitigation (CC9) | Vendor management, business continuity | `subprocessors.md` template | Missing | No vendor reviews or DPAs; no BCP/DR plan |
| Availability (A1) | Capacity, backups, recovery | Backup/restore procedure in `docs/operations-runbook.md` | Partial | Restore test not yet performed; no SLO measurement; single host |
| Confidentiality (C1) | Identify, protect, dispose confidential info | `data-handling-and-retention.md`; project delete with durable tombstone + retrying reaper + `storage.purge` audit record; per-workspace cache | Partial | Reaper tested on SQLite/fake S3 only; backups retain deleted data until expiry |
| Processing integrity (PI1) | Complete, accurate processing | Evidence-or-nothing verification, budget limits, regression and eval gates, idempotent worker (contract) | Partial | Strong for engine output; platform-level reconciliation unverified |
| Privacy (P) | Notice, choice, retention, deletion | Retention draft, deletion API | Missing | Privacy notice, DPA template, data subject request process |
| HR / people | Background checks, onboarding/offboarding, training | n/a (solo) | Missing | Needed before hiring |
| Asset inventory | Systems and data inventory | Compose files, data-handling table | Partial | Maintain inventory incl. vendors |
| Evidence collection | Automated evidence (e.g. via compliance tool) | None | Missing | Choose tooling when engaging an auditor |

## Honest summary
Strong technical groundwork for Security and Confidentiality in the engine (sandboxing, redaction, evidence
verification). The platform controls (tenant isolation, audit, deletion, monitoring) are designed but unverified. The
organisational controls (policies approved, reviews, vendor management, training, DR) are mostly missing. A Type I
readiness assessment is a sensible next step only after the platform is running and the items above are in place.

## Update 2026-11-09
A hostile review produced 15 findings and a deploy bug; see `pentest-2026-11-09.md`. Fixed with regression tests: SSE
exhaustion, denial of wallet, tar directory bombs, deletion completeness, invitations, JWT secret strength, rate-limit
behind proxy, SSRF gaps, headers/races. **Open: F5** (worker holds DB credentials and the model key) and the remainder
of F4 (RLS on memberships/audit/entitlements, composite FKs). Postgres hardening (FORCE RLS, role split, append-only
audit) is written but **unverified**. None of this changes the statement that the project is not SOC 2 compliant.
