# Subprocessor list (TEMPLATE)

Status: **TEMPLATE. No entry below is confirmed.** Every `[PLACEHOLDER]` must be replaced with verified facts and a
signed agreement before this list is published or promised to customers. Do not fill in terms from memory; copy them
from the signed contract.

| Subprocessor | Purpose | Data it can see | Location | Agreement / DPA | Retention terms | Status |
|---|---|---|---|---|---|---|
| Model provider **[PLACEHOLDER: e.g. the provider behind `MODEL_ENDPOINT_HOST`]** | LLM analysis and verification | Redacted code excerpts, prompts, model outputs, user docs text | [PLACEHOLDER: region] | [PLACEHOLDER: DPA signed? date] | [PLACEHOLDER: zero-retention? training use? verified from contract] | Not contracted |
| Hosting / compute **[PLACEHOLDER]** | Runs API, worker, web | All stored data | [PLACEHOLDER] | [PLACEHOLDER] | n/a | Not chosen |
| Managed Postgres **[PLACEHOLDER, may equal hosting]** | Database | Accounts, metadata, results index | [PLACEHOLDER] | [PLACEHOLDER] | Backups [PLACEHOLDER] | Not chosen |
| Object storage **[PLACEHOLDER]** | Source, artifacts, cache | Customer source and results | [PLACEHOLDER] | [PLACEHOLDER] | Versioning/retention [PLACEHOLDER] | Not chosen |
| Identity provider **[PLACEHOLDER]** (when `AUTH_MODE=oidc`) | Login, MFA | Email, identity subject | [PLACEHOLDER] | [PLACEHOLDER] | [PLACEHOLDER] | Not chosen |
| Transactional email **[PLACEHOLDER]** | Invites, notices | Email addresses | [PLACEHOLDER] | [PLACEHOLDER] | [PLACEHOLDER] | Not chosen |
| Error tracking / logging **[PLACEHOLDER]** | Diagnostics | Must receive no source content; scrub rules required | [PLACEHOLDER] | [PLACEHOLDER] | [PLACEHOLDER] | Not chosen |

## Change policy (proposed)

Add or replace a subprocessor only after review of its security posture and signed terms. Notify customers
**[PLACEHOLDER: notice period]** before a new subprocessor sees customer data. Keep a dated change log below.

| Date | Change | Notice sent |
|---|---|---|
| (none yet) | | |
