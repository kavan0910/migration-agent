# Data handling and retention policy (draft)

Status: **DRAFT for owner approval.** Retention periods below are *proposed defaults*, not promises. Nothing in this
document may be shown to customers as a commitment until the owner approves it and the controls marked "not yet
verified" have passed their checks. Sources of truth: `docs/platform-contract.md`, `docs/product-architecture.md`.

## 1. What is stored, where

| Data | Contains | Location | Sensitivity | Proposed retention |
|---|---|---|---|---|
| Account | email, password hash (dev auth) or external identity subject, role | Postgres `users`, `memberships` | Personal data | While account exists; removed on account deletion |
| Customer source | uploaded archive or cloned tree (secrets withheld/redacted at ingest, see `docs/user-guide.md`) | Object store `{workspace}/{project}/source/` | **Highly sensitive (customer IP)** | Deleted with the project; auto-purge **[PROPOSED: 30 days after last run]** |
| Ingested copy and chunks | redacted code, extracted facts | Object store, worker scratch (ephemeral) | Highly sensitive | Scratch: destroyed at end of job. Stored: with project |
| User docs (for Verifier) | customer documentation | Object store `user_docs/` | Sensitive | With project |
| Results | graph, understanding doc, findings, comments, changelog, exports | Postgres index rows + object store | Sensitive, derived from code | With project |
| LLM cache | model responses keyed by prompt hash, **per workspace** | Object store `{workspace}/...cache/` | Sensitive | With project; hard purge **[PROPOSED: 30 days]** |
| Usage events | tokens, USD, model, stage, timestamps (no content) | Postgres `usage_events` | Business | **[PROPOSED: 24 months]** |
| Run events | progress and log lines (must not contain source content) | Postgres `run_events` | Operational | **[PROPOSED: 90 days]** |
| Audit log | actor, action, target, time, metadata | Postgres `audit_log` | Security | **[PROPOSED: 12 months]**; survives project deletion (contains ids, not content) |
| Application logs | structured logs with run/request ids; never source content | log sink **[DECISION NEEDED]** | Operational | **[PROPOSED: 30 days]** |
| Backups | Postgres dumps, object store snapshots | **[DECISION NEEDED: provider]** | Same as source data | **[PROPOSED: 35 days]**; deleted data persists in backups until they expire |

## 2. Data sent to third parties

Customer code excerpts (already secret-redacted, best effort) and prompts are sent to the **model provider** during
analysis. The provider's retention and training terms apply; see `subprocessors.md`. **[NOT YET DONE]** no
zero-retention agreement is in place. Until it is, the product must say so plainly. Secret redaction is pattern based
(`docs/threat-model.md` residual risk 1): novel secrets can reach the provider.

## 3. Deletion guarantees (target design)

`DELETE /v1/projects/{pid}` removes, in one operation: object-store prefix `{workspace}/{project}/` (source, scratch
leftovers, artifacts, per-workspace cache entries for the project), all database rows referencing the project (runs,
run_events, artifacts, findings, comments, graph index), and writes an audit record (ids only). Workspace deletion
repeats this for every project, then removes memberships and the workspace row.

What is NOT removed immediately: backups (until expiry), the audit record, aggregated usage events (no content),
and data already processed by the model provider (governed by provider terms).

Account deletion: **[NOT YET BUILT]** as an end-user feature; manual procedure in the runbook.

## 4. How deletion is verified

Status of each check is stated honestly.

| Check | How | Status |
|---|---|---|
| API-level: deleted project returns 404 and lists nothing | `deploy/smoke.sh` (delete project step) | Written; **not yet run against the real backend** |
| Storage-level: no object under the project prefix | backend test asserting `ObjectStore.list(prefix)` is empty after delete | **Owned by backend stream; confirm it exists** |
| Database-level: no row with the project id in any table | SQL sweep query in the runbook ("Deletion verification"), run after delete in staging | Query documented; not automated |
| Cache: no cache key under the workspace prefix for that project | included in prefix sweep | Depends on cache layout in backend |
| Worker scratch: empty after job, including on crash | worker test + tmpfs (memory-backed, lost on container stop) | tmpfs in compose; test **to confirm** |
| Backups: expiry honoured | restore-test log + retention config review | **Not done** |

## 5. Access to customer data

Operators have no standing access to customer source. Access for support requires the customer's consent and is
recorded in the audit log. **[NOT YET BUILT]** break-glass tooling; today access means direct database/object-store
access by the single operator, which is a known gap (see `soc2-readiness.md`).

## 6. Data location

**[DECISION NEEDED]** hosting region and any data-residency promise. No residency claim may be made until chosen.

## 7. Customer rights and requests

Export: `GET .../export` provides results; full source export is not offered (the customer already has it).
Access/erasure requests for personal data: handled manually through the runbook until automated.
