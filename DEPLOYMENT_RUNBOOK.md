# Deployment, Health, Logs, and Backup Runbook

This repository contains two separate applications: the root Streamlit dashboard/SDK and the nested FastAPI service in satya-ai/. Test and deploy them as separate applications.

## Required environment

- SATYA_AGENT_KEYS: comma-separated unique agent keys, each at least 32 characters.
- AUDIT_SECRET: independent random secret at least 32 characters.
- DATABASE_URL: database URI appropriate to the target environment.
- For Compose/PostgreSQL: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB.

Generate unique secrets in a trusted terminal; do not commit them. Copy satya-ai/.env.example to satya-ai/.env, replace every placeholder, and restrict file permissions with chmod 600 satya-ai/.env. Never use sample values or CI/test values in production.

## Clean test run

Root SDK/dashboard:

    python -m venv .venv-root
    . .venv-root/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -e ".[dev]"
    SATYA_AGENT_KEYS="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" HUMAN_VIEW_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" AUDIT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" PYTHONPATH=. pytest -q tests/

Nested FastAPI service:

    python -m venv .venv-api
    . .venv-api/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -e "satya-ai[test]"
    cd satya-ai
    SATYA_AGENT_KEYS="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" AUDIT_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" DATABASE_URL="sqlite+aiosqlite:///:memory:" pytest -q tests/

## Health and logs

- Container status: docker compose -f satya-ai/docker-compose.yml ps
- API readiness: curl -fsS http://127.0.0.1:8000/health. A healthy response must report both database and audit worker as ok.
- Logs: docker compose -f satya-ai/docker-compose.yml logs --tail=200 satya postgres
- Treat any failing health check, repeated audit-write error, database error, missing secret, or migration failure as a release blocker.
- Check GitHub Actions for successful Root SDK and dashboard tests and Nested FastAPI governance API tests for the exact commit being deployed.

## Backups and restore check

Before every release or migration, take a database backup and verify that it is non-empty and restorable. For PostgreSQL Compose:

    mkdir -p backups
    docker compose -f satya-ai/docker-compose.yml exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "backups/satya-postgres-$(date -u +%Y%m%dT%H%M%SZ).dump"
    test -s "$(ls -1t backups/*.dump | head -1)"

For SQLite, use the online backup API or sqlite3 .backup instead of copying a live database during writes. Back up the root dashboard's satya_data/ directory, including tasks, truth sources, events, heartbeats, and logs. Keep backups outside the repository, encrypt them at rest, restrict access, and define retention.

Restore into an isolated staging database first. Verify task/audit counts and sample task records before scheduling a production restore. A backup command succeeding is not proof of recoverability.

## Rollback

Keep the previous image/tag and a database backup before migrations. If health checks fail, stop traffic, inspect logs, roll back the application to the prior known-good build, and restore the database only when schema/data compatibility requires it. Record the incident and verify health again.

## Scope limitation

This runbook documents checks but does not assert that production environment variables, logs, backups, DNS, TLS, or live deployment have been accessed or verified by this repository audit.
