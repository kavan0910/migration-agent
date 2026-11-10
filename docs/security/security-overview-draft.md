# Security overview (customer-facing DRAFT)

> **DRAFT. Not approved for publication.** Every statement must be re-verified against the running product by the
> owner first. Items in brackets are decisions or facts not yet established. We do not claim certifications.

## What this service does with your code
migration-agent reads your repository to build an evidence-backed understanding document. It never executes your
code. Analysis happens in an isolated worker; excerpts of your code are sent to an AI model provider to produce
the analysis (see "AI model provider").

## Isolation
- Each workspace's data is separated by access controls in the application and database. Resources of other workspaces are indistinguishable from nonexistent ones.
- Analysis runs in a hardened container: non-root, read-only filesystem, no extra privileges, resource limits, no code execution of your repository.
- The analysis container can reach only the model provider's endpoint, through an allowlisting proxy. [Planned: one-time-use container per analysis run; not yet implemented.]

## Secrets in your repository
Known credential files are withheld and common secret patterns are redacted before analysis. Redaction is pattern
based and best effort; unusual secrets can be missed. Please rotate any secret you suspect was exposed.

## AI model provider
Prompts containing redacted code excerpts are sent to **[PLACEHOLDER: provider name]**. Retention and training terms:
**[PLACEHOLDER: state exactly what the signed agreement says. Today no zero-retention agreement is in place.]**
[Planned: bring-your-own-key or self-hosted model endpoint option.]

## Data retention and deletion
You can delete a project; this removes its source, results, cached model responses and database records.
Backups expire after **[PLACEHOLDER: days]**. Details: data handling and retention policy **[link once approved]**.

## Access and audit
Role-based access (owner, admin, member, viewer). An audit log records access to project data. Our staff do not browse
your code; support access happens only with your consent **[confirm the process exists before stating this]**.

## Encryption
In transit: **[PLACEHOLDER: TLS termination is not yet built]**. At rest: **[PLACEHOLDER: depends on hosting provider]**.

## Compliance status
We do **not** currently hold SOC 2, ISO 27001 or any other certification and have not completed an independent
audit or penetration test. We are building toward SOC 2 readiness; our current self-assessment is in
`docs/security/soc2-readiness.md`.

## Vulnerability disclosure
See `SECURITY.md`. Contact: **[PLACEHOLDER]**.

## Subprocessors
**[PLACEHOLDER: link to published list]**
