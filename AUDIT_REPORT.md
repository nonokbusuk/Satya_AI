# Satya_AI Security and Functional Audit

Audit branch: `audit/security-hardening-2026-10`

Pull request: https://github.com/nonokbusuk/Satya_AI/pull/1

## Work applied in this branch

- SSRF guard: DNS answers are checked for public routability and the actual socket connection is pinned to a vetted address. Redirects are fetched one hop at a time and revalidated. Proxy environment variables are ignored for these requests, TLS certificate/hostname checks remain enabled, and response size is limited.
- Webhooks use the same pinned destination strategy, disallow unsafe URL forms and redirects, and log dispatch failures without exposing webhook credentials.
- Audit-chain writes hold a cross-process lock while reading the tail, calculating the HMAC, and appending/fsyncing the record. Stale parent signatures are rejected. Audit signing fails closed when the signing secret is missing, too short, or a known placeholder.
- Dashboard read-only users no longer receive template deployment controls. Several HTML-injection sinks were escaped or converted to allowlisted values. Failed task/goal/knowledge/chat writes produce useful user-facing errors and server-side diagnostic logs.
- Test isolation was improved by removing global dependency mocks and restoring storage/auth state. Added coverage for DNS rebinding, redirects to private addresses, pinned transport, multiprocess audit writes, stale HMAC parents, persisted tasks/comments/knowledge after a fresh process, and dashboard rendering/authorization.
- The nested FastAPI service requires strong configured credentials, checks database and audit-worker readiness, retries audit writes, drains accepted events on shutdown, and reports irrecoverable audit loss as unhealthy.
- CI now has independent root and nested API test jobs plus a Compose configuration validation job. Added root and nested environment examples and an operations runbook.

## Required verification

The GitHub Actions API returned zero workflow runs for this repository/branch during the audit. Therefore these suites have **not been execution-verified here** and no passing result is claimed.

After enabling GitHub Actions for this fork if it is disabled, run the `Analytics and Test Harness` workflow using `workflow_dispatch` on the audit branch. Require all of these jobs to pass before merge:

1. Root SDK and dashboard tests
2. Nested FastAPI governance API tests
3. Validate deployment configuration

The tests are designed to check the regression areas listed above; any failing assertion should be fixed and the workflow rerun. Repository-only access does not verify a live service's environment variables, runtime logs, DNS/TLS, backup files, restore, or production database.

## Production release gates

- Generate independent random values for `SATYA_AGENT_KEYS`, `HUMAN_VIEW_TOKEN` (if using admin mutation features), and `AUDIT_SECRET`; never use sample or CI values.
- Check `/health` reports both database and audit worker healthy.
- Review application/database logs for migration errors and audit write failures.
- Take a backup before migration and perform a restore into an isolated staging database.
- Do not merge/deploy until CI passes and the runtime checks above are confirmed.
