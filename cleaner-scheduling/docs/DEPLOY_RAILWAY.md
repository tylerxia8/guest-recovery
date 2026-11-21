# Railway Deployment Guide

This guide covers the hosted deployment of the cleaning scheduler (airbnb-management-bot)
to Railway, using `infra/railway/backend.Dockerfile`.

## Architecture (lucena-turnover)

```
┌─────────────────────────────────────────────────────────────────┐
│                        Railway Project                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────┐                        ┌─────────┐                │
│  │   api   │                        │   web   │                │
│  │Dockerfile│                       │ Next.js │                │
│  │ :$PORT  │                        │ :$PORT  │                │
│  └────┬────┘                        └────┬────┘                │
│       │                                  │                      │
│       └────────────────┬─────────────────┘                      │
│                        │                                        │
│       ┌────────────────┴────────────────┐                       │
│  ┌────▼────┐    ┌─────────┐       ┌─────▼─────┐                │
│  │Postgres │    │  OSRM   │       │   Redis   │                │
│  │ Plugin  │    │ Service │       │  Plugin   │                │
│  └─────────┘    └─────────┘       └───────────┘                │
└─────────────────────────────────────────────────────────────────┘
```

**One backend service (`api`)** runs in-process scheduling and email automation.
There is no separate Celery worker. The flags `LOCAL_SCHEDULING_ENABLED=true` and
`LOCAL_EMAIL_AUTOMATION_ENABLED=true` enable these features within the API process.

## Service Overview

| Service  | Build                              | Healthcheck          |
|----------|------------------------------------|----------------------|
| api      | `infra/railway/backend.Dockerfile` | `/v1/health/ready`   |
| web      | `frontend/` (npm build)            | `/health`            |
| postgres | Railway plugin                     | —                    |
| redis    | Railway plugin                     | —                    |
| osrm     | Pinned OSRM image                  | —                    |

## Startup Flow (hosted_startup.py)

The Dockerfile runs `python hosted_startup.py`, which:

1. Validates internal-only network (`.railway.internal` hosts)
2. Materializes secrets from environment to `/data/private/` (field key, Gmail JSON, routing manifest)
3. Drops to unprivileged user (uid 10001)
4. Validates `DATABASE_URL.username == cleaning_app`
5. Runs `alembic upgrade head` using `MIGRATION_DATABASE_URL` (postgres superuser)
6. **Pops `MIGRATION_DATABASE_URL` from the environment** before starting uvicorn
7. Starts the app as `cleaning_app` (all sessions subject to RLS)

## Role Validation Guard

At startup, `validate_app_role(engine)` runs unconditionally in `create_app()`.
It refuses to start unless the database connection passes all checks:

| Check | Requirement | Error Code |
|-------|-------------|------------|
| current_user | Must be `cleaning_app` | `app_role_not_cleaning_app` |
| rolsuper | Must be `false` | `app_role_superuser` |
| rolbypassrls | Must be `false` | `app_role_bypassrls` |
| rolcreaterole | Must be `false` | `app_role_createrole` |
| Table ownership | Must not own any RLS table | `app_role_owns_table` |
| FORCE RLS | All tenant tables must have FORCE RLS | `force_rls_missing` |
| Schema revision | Must match expected | `schema_revision_mismatch` |

The guard logs at WARNING level on success:
```
db.validate_app_role current_user=cleaning_app rolsuper=False rolbypassrls=False rolcreaterole=False rolinherit=True
```

**Watch this line in the logs after deploy.** It confirms the app connected as the
intended unprivileged role.

## Deploy Notes

**Merging to `main` auto-deploys.** No environment changes or new roles are needed.

The existing `cleaning_app` role is a LOGIN role with:
- No SUPERUSER, BYPASSRLS, or CREATEROLE
- Grants to specific tables (via migrations)
- Subject to forced row-level security

The app validates it is connecting as exactly `cleaning_app` and that this role
has no dangerous privileges.

### What to Watch After Deploy

1. **Check the role validation log line** (shown above) in the api service logs
2. **Health check:** `GET /v1/health/ready` returns `{"status": "ready", ...}`
3. **Automatic dispatch:** If `LOCAL_EMAIL_AUTOMATION_ENABLED=true`, logs show
   `hosted_startup_ready automatic_dispatch=True`

### Optional Follow-up: Pre-deploy Command

Currently, `hosted_startup.py` runs alembic at boot, which means `MIGRATION_DATABASE_URL`
(the postgres superuser connection) exists in the boot environment.

A cleaner approach moves migrations to Railway's pre-deploy command:
1. Set pre-deploy: `uv run alembic upgrade head` with `MIGRATION_DATABASE_URL`
2. Remove alembic call from `hosted_startup.py`
3. The superuser URL never enters the running app's environment

This requires Isaac's approval since it changes the Railway service configuration.

## Environment Variables

### Required for api

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | `postgresql+psycopg://cleaning_app:...@postgres.railway.internal/db` |
| `MIGRATION_DATABASE_URL` | `postgresql+psycopg://postgres:...@postgres.railway.internal/db` |
| `REDIS_URL` | `redis://default:...@redis.railway.internal:6379` |
| `ROUTING_SERVICE_URL` | `http://osrm.railway.internal:5000` |
| `APP_ENV` | `production` |
| `AUTH_PROVIDER` | `workspace` |
| `WORKSPACE_SIGNUP_ENABLED` | `true` |
| `WORKSPACE_REGISTRATION_ENABLED` | `false` |
| `WORKSPACE_INTERNAL_PAT_ENABLED` | `false` |
| `HOSPITABLE_READ_ONLY` | `true` |
| `LOCAL_SCHEDULING_ENABLED` | `true` |
| `LOCAL_EMAIL_AUTOMATION_ENABLED` | `true` |
| `CLEANER_PORTAL_BASE_URL` | Public HTTPS web origin |
| `API_ALLOWED_HOSTS` | Private hostname, `healthcheck.railway.app` |
| `FIELD_ENCRYPTION_KEY` | (secret) |
| `GMAIL_CREDENTIALS_JSON` | (secret) |
| `ROUTING_MANIFEST_JSON` | (secret) |

### Required for web

| Variable | Value |
|----------|-------|
| `CLEANING_API_ORIGIN` | Private api service URL |

## Troubleshooting

### "app_role_not_cleaning_app" startup error

`DATABASE_URL` username is not `cleaning_app`. Check the connection string.

### "app_role_superuser" startup error

Connected as a superuser (e.g., postgres). Use the `cleaning_app` role in `DATABASE_URL`.

### "hosted_workspace_isolation_required" startup error

One of:
- Database/Redis host doesn't end in `.railway.internal`
- Username in DATABASE_URL is not `cleaning_app`
- Missing required HTTPS cleaner portal URL
- Secret env vars still set in process environment

### Health check returns schema_revision_mismatch

Alembic migrations didn't run. Check api logs for alembic output. If using a fresh
database, the migration should run automatically at boot.

### MIGRATION_DATABASE_URL appears in app environment

Should not happen — `hosted_startup.py` pops it after alembic runs. If you see it
in running process environment, check that the startup script ran correctly.

## Operational Notes

### Backups and Keys

Keep the matching encryption key with every database backup. Railway secret variables
hold the recovery copies; never put them in GitHub or build arguments. Database and
encryption key must stay paired.

### Cutover and Rollback

For cutover, stop the local preview worker before making the final paired database/key
backup, restore into the isolated hosted database, and verify the hosted private
dependencies with dispatch disabled. Enable coordination only after the hosted
workspace is reachable. Keep the stopped local preview and paired backup as the
rollback source; do not run both copies against separate databases with messaging
enabled.

**Rollback:** Revert the merge commit on `main`. Schema stays at `0027_submitting_at`;
no database downgrade is needed.

### Migration and Verification

Disable scheduling and Gmail flags during migration and verification. The SMTP sender
requires Railway Pro or higher.

### OSRM Routing

`ROUTING_SERVICE_URL` points to the private OSRM service, using the existing pinned
OSRM image and matching public road graph. The graph remains regional; houses outside
its coverage require a reviewed map update. No cleaner address or booking data
belongs in the road-graph upload.

### Service Configuration

Disable service sleeping so source polling, evening dispatch and offer deadlines
continue without browser traffic or a laptop. API allowed hosts include its private
hostname and Railway's documented `healthcheck.railway.app`.

### Access Control

This deployment serves the existing internal account only. It does not enable customer
onboarding through shared personal tokens. Hospitable Partner Portal approval and a
verified OAuth adapter remain necessary before opening registration to other property
managers.
