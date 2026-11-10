# Incident response outline

Status: outline for a solo operator. **Never exercised.** First tabletop exercise is an open action.

## 1. Roles
Incident lead = the on-call operator (currently one person). **[PLACEHOLDER]** deputy and legal/privacy contact.

## 2. Severity
| Sev | Meaning | Examples | Response target |
|---|---|---|---|
| 1 | Confirmed cross-tenant exposure, source leak, credential compromise | tenant A reads B's graph; provider key leaked | Start immediately, contain within hours |
| 2 | Likely exposure or sustained outage of core path | worker sandbox escape suspected; DB down | Same day |
| 3 | Degraded service, no data exposure | queue stuck, model provider outage | Next business day |

## 3. Phases
1. **Detect and triage.** Sources: uptime alerts, error tracking, audit log anomalies, user or researcher report (`SECURITY.md`). Open an incident note (time-ordered log, UTC). Assign severity.
2. **Contain.** Typical levers: revoke/rotate keys (runbook "Rotating keys"), disable signups, stop the worker (`docker compose stop worker`), block a workspace, revoke tokens by rotating `JWT_SECRET`, cut egress by stopping `egress-proxy`.
3. **Preserve evidence.** Snapshot logs, `audit_log`, proxy access log, DB state before changing things where safe.
4. **Eradicate and recover.** Patch, rebuild images, restore from backup if integrity is in doubt (runbook "Restore"), verify with smoke test.
5. **Notify.** Affected customers and, where required, regulators. **[PLACEHOLDER: legal review of notification duties and deadlines; do not assume a deadline here.]** Notify the model provider if their key or data was involved.
6. **Post-incident review** within 5 business days: timeline, root cause, what worked, actions with owners. Blameless. Add a regression test or control. Update the threat model.

## 4. Communication
Template: what happened, what data, what we did, what customers should do, next update time. Do not speculate; state unknowns. Status page **[NOT YET BUILT]**.

## 5. Preparedness checklist
- [ ] On-call contact method defined and tested
- [ ] Secret rotation procedure rehearsed once (runbook)
- [ ] Backup restore tested (runbook)
- [ ] Customer contact list reachable from outside the platform
- [ ] Tabletop exercise held; findings recorded here
