# Airbnb Management Bot

Manager workflow: review houses and bookings together under **Hospitable**, confirm **Cleaners**, then activate scheduling and supervise the **Dashboard**. Normal setup does not require duplicate house/reservation entry; cleaning times only need attention when approved defaults are missing or unsuitable. The current connection is a fictional local proxy, and live Hospitable access remains unverified.


**Current scope (2026-11-09):** automate cleaner scheduling only. Payments, compensation setup and the standalone Settings screen are deferred. Their historical contracts and database records remain for possible restoration; they are not active features. Scheduling policies, hours, consent, eligibility, travel and deadlines remain because they determine feasible schedules.

The repository contains the engineering specification and an implementation through M7 using mock integrations and synthetic data: reservation ingestion, cleaning tasks, schedule previews/publication, recorded SMS, the manager dashboard and cleaner completion tracking. No production integration is enabled.

The system coordinates cleaning for one Charlotte, NC property management business with 51–100 properties and 4–10 independent contractor cleaners. Its objective is to reduce manual coordination time by at least 80% while completing turnovers reliably.

## Presentable local demo

For the separate read-only Hospitable workspace, save the token in an owner-only `~/.hospitable-read-token`, start Docker, then run `python3 infra/local/hospitable.py`. Open `http://localhost:3011/reservations` and choose Connect to Hospitable. Imports refresh while the local API runs; account changes, operational scheduling and SMS are disabled. This uses a dedicated database and excludes guest details, street addresses and access codes. See [integration evidence and limits](docs/HOSPITABLE_INTEGRATION.md). It does not use the synthetic demo below.

See [Local demo](docs/LOCAL_DEMO.md) for Windows prerequisites and the walkthrough. After installing dependencies, start from the repository root:

```powershell
backend\.venv\Scripts\python.exe infra\local\demo.py
```

Open **http://127.0.0.1:3108**, choose manager, and select **Enter demo**. The sample clock is November 10, 2026 at 1 PM Eastern. Ctrl+C stops the private demo services; add `--reset` on the next launch to restore synthetic data. This is the M8 local-demo subset; production remains disabled.

## Operational workflow

1. Import reservations through a verified Hospitable adapter; use synthetic fixtures during development.
2. Derive cleaning tasks from departures and the next known arrival. Preserve tasks with no known deadline as pending.
3. Collect next-day availability and starting locations at a proposed 5:00 PM Eastern; proposed cutoff is 5:30 PM, subject to manager approval.
4. Preview a travel-feasible schedule, surface uncovered jobs, honor manager locks, and dispatch the next day's assignments at 6:00 PM Eastern.
5. Require explicit SMS acknowledgment, remind at a proposed 6:30 PM, and escalate at a proposed 7:00 PM. Track completion and replan disruptions.
6. Prepare compensation batches for manager approval and CSV export. Never transfer money.

Core features include deadline-aware routing, cleaner-specific capacity, property instructions with local overrides, schedule versioning, two-way SMS, manual overrides, exception monitoring, audit history, and immutable compensation snapshots.

## Proposed architecture

Next.js/TypeScript manager UI → Python/FastAPI application → PostgreSQL. Celery/Redis execute durable background work; Google OR-Tools solves schedules. Adapters isolate Hospitable, Twilio, Google Maps Routes API, and managed authentication. Deployment targets managed cloud infrastructure; the vendor remains open.

## Documentation

| File | Purpose |
| --- | --- |
| [PRD.md](PRD.md) | Authoritative numbered product requirements and business rules |
| [AGENTS.md](AGENTS.md) | Project-wide implementation instructions |
| [Architecture](docs/ARCHITECTURE.md) | Boundaries, runtime flows, failure handling |
| [Database schema](docs/DATABASE_SCHEMA.md) | Tables, constraints, concurrency, migrations |
| [API specification](docs/API_SPECIFICATION.md) | Proposed internal REST contracts |
| [Hospitable integration](docs/HOSPITABLE_INTEGRATION.md) | Verification gates and mock/fallback contracts |
| [Scheduling engine](docs/SCHEDULING_ENGINE.md) | Solver inputs, priorities, feasibility and publication |
| [SMS workflows](docs/SMS_WORKFLOWS.md) | Commands, templates, acknowledgment, retries |
| [Archived cleaner payments](docs/CLEANER_PAYMENTS.md) | Deferred historical finance contract |
| [Implementation plan](docs/IMPLEMENTATION_PLAN.md) | Eight milestones and requirement traceability |
| [Acceptance tests](docs/ACCEPTANCE_TESTS.md) | Executable scenario specifications |
| [Security](docs/SECURITY.md) | Roles, sensitive data, deployment controls |
| [Open questions](docs/OPEN_QUESTIONS.md) | Decision owners and launch gates |
| [.env.example](.env.example) | Non-secret proposed configuration contract |

## M1 local development

M1 boots a mock-only API, an idle Celery worker, and a Next.js shell. It does not send SMS, publish a cleaning schedule, or transfer money. Production and staging processes refuse to start because verified provider adapters and managed OIDC are not implemented.

Pinned majors used for this milestone: Python 3.12, Node.js 22, PostgreSQL 16, Redis 7. Exact package pins are `backend/uv.lock` and `frontend/package-lock.json`. The local verification machine used Python 3.12.3, Node.js 22.23.4, PostgreSQL 16.15, Redis 7.0.15, and uv 0.13.7.

Create the local database roles and a gitignored `backend/.env`:

```bash
infra/local/bootstrap.sh
```

The database password `local-dev-only` is a local fixture. Do not reuse it anywhere except this machine or ephemeral CI. Leave provider credential variables empty.

Apply the foundation migration and seed fictional operators:

```bash
cd backend
set -a && source .env && set +a
uv sync --all-groups --frozen
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In other shells, from `backend` with the same environment:

```bash
uv run celery -A app.workers.celery_app worker --loglevel=INFO --pool=solo
```

From `frontend`:

```bash
npm ci
npm run dev
```

Liveness is `GET /v1/health/live`. Readiness is `GET /v1/health/ready`. The only business write in M1 is owner policy draft creation and settings approval. Approval records the settings document. It does not enable dispatch, SMS, or transfers.

Denied writes do not insert policy rows, idempotency records, or outbox events. They do append one sanitized `security.access_denied` audit. That audit stores a reason code and route, not tokens, raw bodies, phone numbers, or access instructions.

### M1 local evidence

Commands below were run on the implementation machine after the lockfiles were installed. The same checks passed on hosted GitHub Actions for commit `bb3d96c`: https://github.com/tylerxia8/airbnb-management-bot/actions/runs/42343278768.

Backend, from `backend`:

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

`pytest` result: 48 passed, 1 warning. The warning is Starlette's deprecation notice that `httpx` with `starlette.testclient` should move to `httpx2`. No test failed.

Frontend, from `frontend`:

```text
npm run typecheck
npm run lint
npm run test:e2e
```

Playwright result: 1 passed (`tests/shell.spec.ts`).

Direct package versions resolved by the lockfiles:

- alembic 1.20.1, celery 5.7.0, fastapi 0.162.0, httpx 0.28.1, mypy 2.4.1, psycopg 3.3.8, pydantic 2.14.3, pydantic-settings 2.16.2, pyjwt 2.15.1, pytest 9.2.0, redis 8.1.0, ruff 0.17.4, sqlalchemy 2.1.8, uvicorn 0.54.0
- next 15.5.29, react 19.3.1, react-dom 19.3.1, typescript 6.0.3, eslint 9.39.5, eslint-config-next 16.4.5, @playwright/test 1.65.0
- postcss 8.5.32, forced with the npm `overrides` field because Next.js 15.5.29 depends on postcss 8.4.31 exactly. PostCSS 8.5.32 is outside the advisory range `<=8.5.22` for GHSA-qx2v-qp2m-jg93, GHSA-6g55-p6wh-862q, GHSA-fxqj-rqcc-2cmp, and GHSA-r28c-9q8g-f849. `npm audit --omit=dev` reports 0 vulnerabilities. `npx next build` compiled successfully on Next.js 15.5.29.

`npm audit` without `--omit=dev` still reports 8 moderate minimatch findings under `eslint-config-next`. Those are development-toolchain findings, not the production PostCSS advisory. npm's suggested fix is `npm audit fix --force`, which would install `eslint-config-next@15.5.29` and was not applied.

### Partial acceptance

M1 does not claim full AT-23, AT-30, or AT-34. It covers the API authorization subset, the flag and startup subset, and log redaction. Dashboard authorization, cleaner links, backup restore, and RPO/RTO remain later milestones. M2 has not started.

## M2 reservation ingestion

M2 imports fictional reservations through the mock provider, CSV preview/commit, and property instruction versions. It does not create cleaning tasks, publish schedules, send SMS, or call Hospitable.

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

Local result after the M2 implementation: Ruff and mypy passed. Pytest: **61 passed, 1 warning**. The warning is the existing Starlette `httpx` TestClient deprecation. `uv run alembic upgrade head` applies `0002_reservations` after `0001_foundation`.

`python -m app.seed` inserts synthetic operators and 102 fictional properties (`synthetic-property-001` through `synthetic-property-100`, plus `P-A` and `P-B`). Ten fictional cleaner names exist only in the fixture module. They are not database rows.

Hosted CI for this milestone is recorded on the M2 pull request after the push. Production Hospitable access remains unverified.

## M3 cleaning tasks

M3 derives one departure task from each active reservation. The release is checkout. The deadline is the next active check-in minus the readiness buffer on an approved policy row. No approved policy means no invented deadline. Pending tasks are not dispatchable. This milestone does not preview, publish, or assign work.

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

Local result: Ruff and mypy passed. Pytest: **69 passed, 1 warning** (Starlette `httpx` TestClient deprecation). `uv run alembic upgrade head` applies `0003_cleaning_tasks` after `0002_reservations`.

`npm audit` without `--omit=dev` still reports 8 moderate minimatch findings under the ESLint development toolchain. They are not production runtime dependencies. `npm audit --omit=dev` stayed at 0 vulnerabilities after the earlier PostCSS override. This milestone does not change frontend dependencies.

## M4 scheduling

M4 previews and publishes schedules for fictional properties only. Routing is the deterministic mock matrix. Publication writes database rows and outbox records and refuses unless every provider is mock and the production dispatch and integration flags are off. It does not call the SMS gateway. The 18:00 America/New_York clock is a function tested with an injected clock. Celery beat stays empty.

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

Local result on this milestone: Ruff and mypy passed. Pytest: **90 passed, 1 warning** (Starlette `httpx` TestClient deprecation). `uv run alembic upgrade head` applies `0004_scheduling` after `0003_cleaning_tasks`. A 100-task, 10-cleaner preview measured **0.385 seconds** and was labeled `feasible_not_proven_optimal`.

Coverage uses OR-Tools CP-SAT. Travel, breaks, caps, and locks are applied by a deterministic sequencer, then an independent validator recomputes them before publication. A non-positive solver limit is labeled `feasible_not_proven_optimal`, never optimal. Scenario policies used by tests are labeled fixtures and are not a production approval. Without an approved policy, preview returns 403 and the dispatch clock does nothing.

Frontend dependencies are unchanged. The previously recorded 8 moderate minimatch development-tooling findings remain distinct from `npm audit --omit=dev`.

## M5 mock SMS

M5 records the SMS lifecycle on the in-process mock gateway. Inbound commands use the mock signature and fictional `+1555555xxxx` numbers. Production Twilio is not constructed. Celery beat stays empty. Reminder and escalation timers run only when a test or operator calls them with an injected clock.

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

Local result: Ruff and mypy passed. Pytest: **102 passed**, 1 Starlette `httpx` TestClient deprecation warning. `uv run alembic upgrade head` applies `0005_messaging` after `0004_scheduling`.

The live dispatch gate still refuses every send. Mock recording also refuses when the app is not the isolated test/development environment, a provider is not mock, a provider credential is present, a production flag is on, or no approved scenario policy exists. Recorded messages use `provider=mock` and are not carrier deliveries. No socket is opened by the recorder.

`npm audit --omit=dev` remains 0. Full `npm audit` still reports 8 moderate minimatch findings in the ESLint toolchain. Frontend dependencies were not changed.

## M6 dashboard

The manager UI reads the local API. It shows today and tomorrow, availability, assignment and acknowledgment counts, completion, sync freshness, SMS delivery counts, uncovered and pending work, and an exception queue. Payment preparation stays off and the queue count is zero. Viewers do not receive that count. Production flags cannot be switched on from the screen.

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest
cd frontend && npm run typecheck && npm run lint && npx next build && npm run test:e2e
```

Local result: backend Ruff and mypy passed. Pytest: **104 passed**, plus the Starlette `httpx` TestClient warning. Frontend typecheck, lint, production build, and Playwright (**3 passed**) passed against the mock API. No migration was added.

`npm audit --omit=dev`: 0 vulnerabilities. Full `npm audit`: 8 moderate minimatch findings in the ESLint toolchain.

## How the implementation agent should continue

Continue with M8 readiness review after the M7 change is accepted. Keep mock integrations and synthetic data. Do not send real SMS, publish an operational schedule, prepare real contractor liabilities, or enable production dispatch. Earlier milestone checks cover documented subsets, not every production requirement.

## M7 payment preparation (historical; removed from active app 2026-11-09)

`/payments` lets managers review completed synthetic work by half-open Eastern service-date period, prepare a draft, inspect cleaner totals and immutable compensation snapshots, approve or void with a reason, and download an approved CSV. Owners can create property overrides/default rates and replace an unused future rate period. Corrections reference approved original lines and use globally unique correction keys. Approval and export never move money.

Migration `0006_payments` adds batches/lines, global earned-work uniqueness, database-enforced approved-history protection, and effective-rate range exclusions. Existing migrations are unchanged. Voiding preserves a frozen historical view; re-preparation moves the same earned-work lines, including corrections when re-preparing the same period, instead of duplicating them. Negative cleaner subtotals cannot be approved. Invalid snapshots or disputed completions are excluded with explicit exceptions. Scheduling now selects overrides only for the matching property and snapshots their rule version.

Backend checks: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app`, `uv run pytest`. Frontend checks: `npm run typecheck`, `npm run lint`, `npx next build`, `npm run test:e2e`. The current M7 verification evidence is recorded in the implementation plan.

For browser tests, use a **fresh isolated test database**, apply migrations, run `python -m app.seed`, then `python -m tests.seed_browser` with `APP_ENV=test` and mock providers before starting the API. The fixture seeds completed fictional work; never run it against an operational database. CI performs these steps automatically.

Windows verification may use `TEST_DATABASE_URL`, `TEST_MIGRATION_DATABASE_URL`, and `TEST_REDIS_URL` to point Pytest at isolated services; tests truncate their configured database and Redis database. The browser server accepts `PLAYWRIGHT_PORT` (default 3000), and the server-side proxy/browser tests accept `CLEANING_API_ORIGIN` (default `http://127.0.0.1:8000`). These settings avoid conflicts with existing local apps. The M7 local check uses Python 3.12.13, Node 22.23.4, PostgreSQL 17, and Redis 8; hosted CI remains PostgreSQL 16/Redis 7 and must also pass before merging.

Later credentials/configuration: authorized Hospitable access or approved feed/import, Twilio account and registered sender, Google Maps key, managed auth issuer/audience, PostgreSQL, Redis, and cloud secret storage. Hospitable API eligibility is an unresolved launch dependency. No provider URLs, authentication schemes, or webhook payloads are asserted here.

Production integrations and SMS dispatch remain disabled until credentials, manager policy approvals, consent/registration, and pilot gates pass. Bank transfers are outside the initial release. Do not merge the initial documentation PR automatically.
