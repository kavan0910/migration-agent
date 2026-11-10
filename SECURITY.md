# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately. Do not open a public issue.

- Preferred: GitHub private vulnerability reporting on this repository (Security tab, "Report a vulnerability").
- Alternative: email **[PLACEHOLDER: security contact address, to be set by the owner before any public launch]**.

Include affected version or commit, reproduction steps, impact, and whether you tested against a hosted instance or
a local build. Do not include real customer data. Do not send secrets; if you found one, say where, not what.

## What to expect

These are targets of a small team, not contractual commitments:

| Step | Target |
|---|---|
| Acknowledge receipt | within 3 business days |
| Initial assessment and severity | within 10 business days |
| Fix or mitigation plan communicated | within 30 days for High/Critical |
| Coordinated disclosure | after a fix is available, or 90 days, whichever is sooner, by agreement |

## Scope

In scope: this repository (engine, backend, web, deploy files), and any hosted instance we operate once one exists.
Out of scope: social engineering, physical attacks, denial of service by volume, findings that need a
compromised host or already-privileged account, third-party services (report those to the vendor), automated scanner
output without a demonstrated impact.

## Safe harbour

Good-faith research that respects this policy, avoids privacy violations and service disruption, and stops at proof
of concept will not be met with legal action by the maintainers. This is a statement of intent by the maintainers,
not legal advice, and does not bind third parties.

## Supported versions

Only the latest released version and `main` receive fixes. There is no bug bounty.

## Status

No security audit, penetration test or compliance certification has been performed. See
`docs/security/soc2-readiness.md` for the honest current state.
