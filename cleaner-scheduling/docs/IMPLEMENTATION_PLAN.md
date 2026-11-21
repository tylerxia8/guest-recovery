# Implementation plan for the scheduling system

M8 short-notice booking readiness (2026-11-12), still in progress: manager chose earlier vacant-house cleaning rather than reserving a percentage of capacity. Automation considers remaining work today before tomorrow, retaining confirmed-turnover/check-in priority and the hard 48-hour ceiling. Existing assignments on another date remain untouched. Valid ongoing and completed work can occupy fixed same-cleaner windows during additive replanning, including conservative actual completion occupancy; stored historical travel, times, statuses and acknowledgments remain unchanged. Blocked work, unfinished overruns, changed historical eligibility/routing or infeasible occupancy still require review. Future-tail emails can report safe additions without repeating solely because jobs started or finished; routine next-day delivery stays at 8 PM Eastern. Independent review corrected shared-place eligibility leakage by retaining conservative historical eligibility checks. Integrated Ruff lint/format (162 files), strict mypy (104 files), backend image and diff check passed. Existing pytest stopped before its first test body at the unscoped operator_profiles RLS fixture; no tests/fixtures were edited and no statistical gain or end-to-end regression is claimed. Backed up the private database/key and updated the local preview. No Hospitable mutations, Railway deployment or merge were performed.

M8 Spanish/Portuguese cleaner accessibility (2026-11-12), still in progress: translated private onboarding, cleaner portal actions/notices/errors/statuses/date displays and onboarding/assignment/change/cover/portal/sample email templates. Names and addresses remain unchanged. Additive migration 0023 stores optional preferred language separately from reading capabilities, with manager and private-link versioned editing. Unknown language uses trilingual email; canonical semantic content and normalized legacy metadata avoid duplicate unchanged English sends. Explicit query/stored/manual display choices survive delayed profile loading; supported language attributes aid screen readers. Exact proxy, workspace-auth and read-only allowlists include only the new authenticated language endpoint, and the missing manager language-write allowlist was repaired. Independent review passed after correcting invitation locale continuity, historical email deduplication and repeated multilingual content. Integrated Ruff lint/format (162 files), strict mypy (104 files), backend/frontend builds, migration upgrade → downgrade to 0022 → upgrade and diff check passed. Existing pytest stopped before its first test body at the unscoped operator_profiles RLS reset fixture; tests/fixtures were not edited. Backed up the private preview database and key, migrated and restarted locally; existing preferences remain unset. Native browser access was unavailable, so visual/end-to-end language behavior is not claimed. No Railway release or merge is claimed.

M8 early completion rule (2026-11-12), still in progress: manager explicitly permits cleaners to mark their own active published/accepted jobs done before the planned start. Removed the backend time gate and matching portal hint; the server capability now enables the button for eligible assignment states. Actual completion timestamps, ownership, task state, version checks, idempotency and auditing remain unchanged. Ruff lint/format, strict mypy (103 files), backend/frontend builds and diff check passed. No completion was submitted on a cleaner's behalf. The existing regression fixture blocker remains; tests and fixtures were not edited. Updated the local signup preview only.

M8 8 PM dispatch and reservation-change emails (2026-11-12), still in progress: initial daily schedule email is gated in queue and drain until 20:00 Eastern the preceding night, while workspace planning remains immediate. Confirmed imported booking changes invalidate unsafe unlocked future work, protect locked/started assignments for review, withdraw obsolete offer dates and durably notify affected previously emailed cleaners. Empty-day removals receive factual corrections; replacement schedules are included only with a current independently validated plan. Coalescing, latest-notified content transitions, stale queued-message checks and unchanged accepted/uncertain submission handling prevent repeated or obsolete instructions. New snapshots record task membership; older snapshots use a conservative creation-time fallback. Onboarding invitations and cover offers remain immediate. Integrated Ruff lint/format, strict mypy (103 files), frontend/backend builds, Compose configuration and diff check passed. Existing pytest stopped before its first test body at the unscoped operator_profiles RLS reset fixture; no tests or fixtures were edited. Independent review passed; a booking extension was not simulated or changed in Hospitable. Backed up the signup-preview database and matching key and restarted its local app; runtime reports 20:00 America/New_York and existing accepted-email counts remained unchanged after restart. The older local business runtime also received the 20:00 environment setting without a data/schema migration. No Railway deployment or merge is claimed.

M8 cleaner reading languages (2026-11-12), still in progress: additive migration 0022 stores optional unique English/Spanish/Portuguese codes with unknown defaults for existing cleaners. Manager creation, roster editing and private onboarding support multiple selections; tenant ownership, idempotency compatibility, cleaner version checks and redacted audit records protect writes. Language-only manager edits do not bump scheduling days or send messages. Integrated backend Ruff lint/format and strict mypy (103 files), frontend production build including lint/types, backend image and diff check passed. Existing migration commands completed upgrade → downgrade to 0021 → upgrade in disposable PostgreSQL. Existing pytest stopped before its first test body at the known unscoped operator_profiles RLS reset fixture; no tests or fixtures were edited. Backed up the private signup-preview database and matching encryption key, stopped its API writer, migrated and restarted the local preview. Existing language choices were not invented. No Railway deployment or merge is claimed.

M8 automatic cleaner acceptance (2026-11-12), still in progress: replaced the blanket changed-revision manager gate with independent validation of the accepting cleaner's fixed daily route. Current source freshness, published day, approved policy, starting routes, hours, breaks, capacity, pending-job eligibility/exceptions, completed occupied time and cross-cleaner house overlap remain binding. Acceptance does not rewrite the global automation marker or reschedule jobs. Fixed pinned-route sequencing to calculate each inbound leg from the preceding house, allowing valid preserved plans to refresh. Independent review corrected historical eligibility leakage and sibling-job exception handling. Integrated Ruff lint/format, strict mypy (102 files), backend image and diff check passed. Existing `pytest --maxfail=1 --tb=short` stopped before the first test body at the raw-session operator_profiles RLS fixture error; no tests or fixtures were edited. Updated the isolated local preview; its automatic published-day revision and marker matched after the worker refreshed. No acceptance or completion action was submitted for a cleaner. The PR remains unmerged and production rollout is not claimed.

M8 cleaning priority refinement (2026-11-12), still in progress: manager-directed same-day turnover → earliest actual next check-in → earliest original checkout now supersedes coverage-first selection. Eastern dates define same-day turnover; missing arrivals follow known check-ins. Greedy sequencing, bounded coverage stages, joint routing and candidate comparison use the same priorities without relaxing locks or hard feasibility. Existing assignments remain protected. Reservation arrival facts enter publication fingerprints; changed arrivals invalidate affected scheduling days even when the 48-hour cap keeps their deadlines unchanged. Independent review found no remaining change-specific blocker. Ruff lint/format, strict mypy (102 files), backend image build and diff check passed. Existing `pytest tests/test_scheduling.py --maxfail=1 --tb=short` stopped at the unscoped reset fixture with an operator_profiles RLS violation; no test body ran. No tests or fixtures were written or edited. Merge and production rollout remain blocked by the red regression gate.

**Current scope (2026-11-09):** automate cleaner scheduling only. Payments, compensation setup and the standalone Settings screen are deferred. Their historical contracts and database records remain for possible restoration; they are not active features. Scheduling policies, hours, consent, eligibility, travel and deadlines remain because they determine feasible schedules.

Begin with **mock integrations and synthetic data**. Do not build production adapters from guessed API behavior. Production integrations remain disabled until authorized credentials/configuration and verified contracts are supplied; production dispatch has a separate default-off flag. Implement one milestone at a time, updating status and evidence below. Proposed paths are targets for future implementation, not application files in this documentation delivery.

## Progress

M8 immediate workspace scheduling (2026-11-12), still in progress: completed manager setup and cleaner onboarding wake the local automation worker after committed writes. Enabled nonlegacy workspaces plan tomorrow immediately; readiness requires saved weekly hours, starting address and verified route data, including owned origins for dated overrides. Changed inputs can add work while frozen assignment snapshots preserve existing IDs, times, cleaners, acknowledgments and locks. Publication independently validates the saved plan, verifies frozen rows under lock and audits derived travel changes. Successful placement resolves obsolete scheduler coverage exceptions without clearing unrelated issues. Schedule-email counts exclude onboarding invitations, and the dashboard describes onboarding-triggered timing. Legacy timing remains unchanged. Ruff lint/format, mypy, backend image and frontend production build passed; the existing suite remains blocked by the raw-session fixture RLS violation. No tests or fixtures were authored or edited.

Authorized local activation evidence: backed up the signup-preview database, matching field key and prior private environment; connected its isolated network to the existing read-only OSRM road service and mounted the road manifest read-only. Enabled free routing, local scheduling and assignment email automation for that preview only. Both previously onboarded cleaners obtained ready routes. The worker published six assignments for 2026-11-13 and Gmail accepted two schedule emails, separately from the two earlier onboarding messages. The active plan retained 24 uncovered entries rather than inventing feasible coverage. This is one observed operational path, not full regression or repeated-onboarding coverage. Hospitable access remained GET-only and SMS remained disabled.

M8 emailed cleaner onboarding (2026-11-12), still in progress: one Create cleaners & finish action saves drafts before completing setup. Completion transactionally queues initial onboarding invitations in the existing encrypted delivery ledger; the separate explicit onboarding flag and worker do not enable assignment automation or SMS. Dashboard notices distinguish pending, provider-accepted, failed and uncertain outcomes. Ruff lint/format, mypy (102 files), frontend production build with lint/types, Compose configuration and migration upgrade/downgrade/upgrade passed. Existing `pytest --maxfail=1 --tb=short` on disposable PostgreSQL/Redis stopped at the unchanged raw-session reset fixture with `new row violates row-level security policy for table "operator_profiles"`; no tests or fixtures were created or edited. No SMTP submissions or end-to-end invitation delivery were performed during verification. The red gate and hosted/partner integration blockers remain open.

M8 sequential signup refinement (2026-11-12): account and connection fields are progressively disclosed behind one primary action; marketing, stepper and app navigation are removed from signup. Backend-validated page guards and business API guards prevent dashboard access before committed completion. Saved cleaners and valid onboarding links reload without creating duplicate records. Completed workspaces remain accessible during later provider failures. Existing partner authorization, hosting and test-fixture blockers below still apply; signup remains default-off.

M8 workspace signup preparation (2026-11-12), still in progress: separate manager accounts and database-enforced workspace ownership; legacy records preserved; resumable account → Hospitable → cleaners screens; encrypted workspace credentials, isolated routing factories, and workspace-bound cleaner links. Signup and internal PAT entry are default-off. Customer authorization awaits Hospitable partner OAuth access and verified protocol details; hosted real-data startup guards remain intact. No Hospitable changes or emails were sent during this work. Ruff lint/format, mypy (101 files), frontend production build including lint/types, Compose configuration, and empty-database migration upgrade/downgrade/upgrade passed in disposable containers. Existing `pytest --maxfail=1` stopped with one setup error: `new row violates row-level security policy for table "operator_profiles"` because the reset fixture creates an unscoped raw session. Tests/fixtures were not edited under the standing user rule. This red gate blocks merge and rollout; new signup behavior and populated legacy migration preservation have not been exercised end to end.

| Milestone | Status | Evidence / blockers |
| --- | --- | --- |
| M1 | Done | Foundation tables only. API auth, flag/startup, and log-redaction subsets. Local and hosted checks passed: 48 pytest, 1 Playwright, Ruff, mypy, tsc, and ESLint. Hosted run https://github.com/tylerxia8/airbnb-management-bot/actions/runs/42343278768 on `bb3d96c`. Full AT-23, AT-30, and AT-34 are not claimed. |
| M2 | Local checks passed | Mock ingestion, CSV import, instruction overrides, and disabled Hospitable stub. 61 pytest passed locally. Task derivation, previews, dispatch, and SMS delivery regression are not claimed. Live Hospitable remains unverified. |
| M3 | Local checks passed | Task derivation from mock reservations. 69 pytest passed locally. Preview, assignment, and dispatch are not claimed. |
| M4 | Local checks passed | Mock routing, OR-Tools coverage, validator, synthetic publication, and injected 18:00 clock. 90 pytest passed locally. A 100x10 preview measured 0.385s. No SMS, live beat, or operational publication. |
| M5 | Local checks passed | Mock SMS lifecycle, versioned CONFIRM, STOP, DONE, reminders, and scoped links. 102 pytest passed locally. No carrier send, live beat, or Twilio client. |
| M6 | Local checks passed | Dashboard and exception queue over mock data. 104 pytest and 3 Playwright passed locally. Payment batches and live dispatch are not included. |
| M7 | Archived; removed from active scope 2026-11-09 | Hosted CI on code commit `e378ad7`: 118 Pytest and 6 Playwright passed on PostgreSQL 16/Redis 7. Local lint/typecheck/build and payment regressions passed. [Evidence](https://github.com/tylerxia8/airbnb-management-bot/actions/runs/42401051115). Production remains disabled. |
| M8 | In progress: default house eligibility, read-only Hospitable imports, encrypted local cleaner setup, cleaner removal, simplified cleaner inputs, typed starting addresses, reusable weekly cleaner hours and next-day view, condensed Hospitable-first manager workflow, scheduling-only scope, saved driving baseline, daily cleaning board, evening demo schedules and bounded optimization | Isolated synthetic launcher, role sign-in, dashboard, schedule publication and mock cleaner simulator. See [manager setup](MANAGER_SETUP.md), [demo guide](LOCAL_DEMO.md) and [open-source evaluation](OPEN_SOURCE_EVALUATION.md). Production acceptance and launch approvals remain open. |

M8 local read-only Hospitable integration (2026-11-10), still in progress: verified official source schemas and manager-authorized account GET access; added an isolated Docker workspace with a dedicated database, backend-only token file, private local application session, GET-only source client, periodic/manual idempotent imports, provenance and quarantine. The first account import read 82 houses and stored 251 accepted/cancelled reservations; seven nonaccepted bookings require review. Guest details, street addresses, coordinates and access instructions are excluded. Source listing visibility does not grant scheduling eligibility. No operational scheduling, upstream mutations, webhooks or SMS are enabled. Bounded-window discovery is not a complete occupancy snapshot. See HOSPITABLE_INTEGRATION.md for launch, pause, backup and contract evidence.

Verification: backend Ruff lint and mypy passed; frontend TypeScript, ESLint and production build passed. The full existing backend suite, on isolated PostgreSQL 16/Redis 7 with the proper repository layout, returned 122 passed and 29 failed; an unchanged checkout of GitHub commit `c9f4801` returned the same 122 passed/29 failed and the same failing scheduling/SMS scenarios. The initial backend-only container mount additionally caused three repository-path failures, corrected by running with the full repository mounted. No test files were added or edited. The red regression gate remains unresolved and M8 is not complete. Browser regression evidence for this change is not claimed.

M8 secure local setup (2026-11-10), still in progress: new writes use authenticated AES-GCM/HKDF field encryption with a separate private key, preserved legacy reads, process-stable key material, launcher fingerprint protection and matching key/database backups. Enabled local cleaner creation/removal, eligibility, weekly hours/address storage, optional job limits and cleaning-duration overrides. Shared publication locks and keyed idempotency protect sensitive setup writes. Addresses remain local; the manager has no Google Maps project and selected preparation only. Scheduling, SMS, policy approval and all upstream writes remain disabled. Existing live records were retained; no real cleaner was fabricated or added during verification.

The existing full backend suite returned 122 passed, 29 failed and one dependency warning. These are the same failing scenarios as the earlier unchanged baseline: the client fixture uses SystemClock against hardcoded November 10 turnovers whose deadlines have elapsed. Past-deadline guards remain correct; fixing the test clock requires test edits prohibited by the standing user instructions. No tests or fixtures were created or edited. A final existing-suite run over configuration, cleaner inputs, weekly hours, setup, authorization and idempotency passed all 39 checks, with one dependency warning. Backend Ruff lint/format and mypy, frontend TypeScript/ESLint/build passed. This does not close M8 or the red regression gate, and no new browser acceptance coverage is claimed.

Commands executed in the isolated Python 3.12/PostgreSQL 16/Redis 7 and Node 22 containers, from backend/frontend respectively:

```text
uv run python -m ruff check .
uv run python -m ruff format --check .
uv run python -m mypy app
uv run python -m pytest --tb=short
uv run python -m pytest tests/test_config.py tests/test_cleaner_inputs.py tests/test_weekly_hours.py tests/test_setup.py tests/test_authz.py tests/test_idempotency.py --tb=short
npm run typecheck
npm run lint
npx next build
```

M8 default eligibility and handoff requirements (2026-11-10), still in progress: migration `0010_default_eligibility` preserves existing explicit allowlists and makes new cleaners eligible for all current/future active houses except explicit exclusions. Setup and solver inputs share the rule; manager-only exclusion edits use idempotency, optimistic versions, audit and the publication lock. Inactive-house exclusions survive reactivation, and the UI no longer requires selecting every house. Old writers stop before applying the additive migration. The manager approved a 120-minute response window for the future task-level punt/claim/manager-alert flow; its requirements are recorded in PRD/scheduling/SMS contracts, but no PUNT/TAKE parser, offer state or alert timer is implemented or enabled in this change. Scheduling and SMS remain disabled.

Validation: backend Ruff lint/format and mypy pass; frontend TypeScript, ESLint and production build pass after correcting an effect-state lint failure and preventing cleaner switching during saves. Existing full Pytest suite: 119 passed, 32 failed, one dependency warning. Failures are the 29 inherited expired-turnover scenarios plus two assertions hardcoding schema `0009_cleaner_inputs` and one exact OpenAPI route-set assertion that omits the new eligibility endpoint. A final targeted run of setup, cleaner inputs, weekly hours, idempotency, authorization, configuration and migration round-trip passed all 42 existing checks, with one dependency warning. Tests were not edited under the standing no-tests instruction. This is an unresolved red gate; no milestone completion or new automated coverage is claimed.

## M1 — Project foundation

Objective: establish reproducible isolated development and enforce architecture/security boundaries.

Dependencies: PRD/contracts; no live accounts required.

Tasks: choose/pin supported Python/Node/PostgreSQL versions; create FastAPI and Next.js skeletons, lockfiles and CI; configure Pytest/Playwright and lint/type checks; implement typed DTO/errors, clock, mock auth/RBAC and adapter interfaces; set default-off feature flags and configuration validation; create initial Alembic migrations for schema with audit/inbox/outbox/idempotency; provide local DB/Redis startup instructions. Keep business data synthetic.

Expected modules: `backend/app/{api,domain,adapters,models,workers}`, `backend/migrations`, `backend/tests`, `frontend/app`, `frontend/tests`, `.github/workflows`, `infra/local`, dependency/config files.

Tests: config fails closed; role denial; adapter contracts; empty-DB migration; idempotency digest conflict; local smoke in CI. Acceptance AT-23, AT-30, AT-34 security/config subsets.

Definition of done: clean checkout can boot local services and run documented checks; DB migrations reversible where safe; no external calls by default; CI passes actual recorded commands.

Deliverables: runnable local foundation, CI workflow, synthetic seed generator, initial migrations, updated README commands and evidence.

Confirmed scope for this milestone: the first migration contains only `operator_profiles`, `policy_versions`, `audit_logs`, `webhook_events`, `outbox_events`, and `idempotency_records`. Domain tables arrive with later milestones. M1 claims the API authorization subset of AT-23, the flag and startup subset of AT-30, and the log-redaction subset of AT-34. A denied write leaves business rows, idempotency records, and `outbox_events` unchanged, and appends one sanitized `security.access_denied` audit. Full dashboard authorization, cleaner links, and the restore drill are not part of this milestone. Local command evidence is in the README. Hosted CI for the implementation commit passed: https://github.com/tylerxia8/airbnb-management-bot/actions/runs/42343278768.

## M2 — Reservation ingestion

Objective: ingest reliable normalized reservation state without duplicate or regressive updates.

Dependencies: M1; official API verification only for optional live adapter later.

Tasks: mock provider and fixtures, capability registry, sync runs/cursors, webhook inbox, CSV preview/commit and authorized feed scaffold; ordering/deduplication rules, quarantines, reconciliation, stale health and outage recovery; imported instruction provenance/local overrides. Verify official contracts before creating production Hospitable implementation; record evidence, do not block mock work.

Expected modules: `adapters/reservations/{base,mock,hospitable,calendar,import_csv}`, `services/reservation_sync`, `workers/reservations`, `api/reservations`, migrations/fixtures/contract tests. `hospitable` may remain a disabled capability stub with an explicit error until verified, never a fake live client.

Tests: AT-01, AT-02, AT-04, AT-05, AT-18, AT-26, AT-29, AT-35, plus pagination/cursor rollback and invalid timestamp quarantine.

Definition of done: repeated/reordered fixture ingestion converges; failed partial runs cannot infer deletion/advance unsafe cursor; sync health and audit are accurate; no guest data persisted.

Deliverables: working mock/import ingestion, replayable contract tests, verification register with truthful status.

M2 scope boundary: ingestion, quarantine, cursors, CSV preview/commit, instruction provenance, and the disabled Hospitable stub. Cleaning tasks, schedule previews, dispatch, and SMS delivery state stay in later milestones, so AT-01, AT-02, AT-04, AT-05, AT-18, AT-26, and AT-29 are claimed only for their reservation, cursor, audit, and instruction facts. Date-only timestamps are rejected because the proposed 10:00/16:00 interpretation is not an approved policy. Complete snapshots do not infer deletions. Calendar feeds are an SSRF guard with no live fetch. The Hospitable verification register remains pending.

## M3 — Cleaning task generation

Objective: produce one correct departure task with honest time windows and duration.

Dependencies: M2 canonical reservations and M1 schema.

Tasks: derive departure/predecessor tasks transactionally; same-day deadlines and readiness buffer; 48-hour deadline cap when no next arrival; explicit manager targets; property duration overrides and configurable bedroom defaults; cancellations/extensions and completion preservation; instruction version selection; audit and task revision outbox.

Expected modules: `domain/tasks`, `services/task_generation`, `api/tasks`, `services/instructions`, tests and migrations.

Tests: AT-03–AT-06, AT-27, AT-28, AT-29; source cancellation versus next-arrival cancellation; missing duration exception.

Definition of done: task uniqueness and predecessor updates survive replay and transaction rollback; deadline-less work cannot be silently dispatched; completed task history is retained.

Deliverables: task generation service/API, synthetic timeline fixtures, requirement-linked tests.

M3 scope boundary: one departure task per reservation, release at checkout, deadline at the earlier of the next active check-in minus an approved readiness buffer and checkout plus 48 elapsed hours (PRD 2026-11-07 revision supersedes the indefinite pending rule), manager targets, duration from a property override or an approved bedroom map, cancellation of unstarted tasks, and preservation of completed tasks. Readiness buffer and bedroom defaults are read only from an approved policy row. With no approved policy, deadlines are not invented. AT-03 and AT-06 claim the deadline facts only; preview validation, publication, and assignment are M4. AT-29 schedule invalidation remains M4. Date-only reservation times stay rejected.

## M4 — Scheduling optimization

Objective: generate/publish independently validated feasible schedules under all hard constraints.

Dependencies: M3; configured mock cleaners/eligibility; deterministic mock routing.

Tasks: dated availability/origins/caps; routing adapter/cache and unreachable handling; OR-Tools model and lexicographic stages; preview snapshots, independent validator, stale fingerprint detection; locks/cross-date occupancy; transactional publication/version history/outbox; partial coverage exceptions; 18:00 local idempotent scheduler; targeted replan for booking/unavailability changes. Production Google adapter only after verified permissions/caching semantics.

Expected modules: `domain/scheduling`, `services/{routing,schedules,availability}`, `workers/{solver,dispatch_clock}`, `api/schedules`, mock maps fixtures and concurrency tests.

Tests: AT-03, AT-06–AT-11, AT-15, AT-16, AT-17, AT-24, AT-25, AT-27, AT-32; solver timeout never falsely labeled optimal.

Definition of done: 100-property/10-cleaner synthetic load measured; every published plan passes independent validator and freshness check; locks unchanged; no SMS from previews; infeasible jobs explicit.

Deliverables: deterministic solver, versioned previews/publication, routing contract tests, performance report.

M4 scope boundary: cleaners, dated availability, rates, routing snapshots, schedule days/runs, cleaner schedule versions, and assignments. Preview is synchronous and returns 200 because no live solver worker is running. Publication is allowed only for mock providers with production dispatch and integration flags off and no provider credentials; it writes local rows and `schedule.published` outbox records and does not call SMS. The dispatch clock is `workers/dispatch_clock.py`, invoked with an injected clock, and is not on Celery beat. It emits one idempotent `schedule.dispatch_clock` intent at the approved policy's local dispatch time and does not publish a schedule. With no approved policy it does not invent a dispatch. CP-SAT maximizes coverage; a deterministic sequencer applies travel, breaks, caps, and locks. Timeout or a non-positive limit is `feasible_not_proven_optimal`. Origins are `synthetic:` routing keys stored as ciphertext plus `origin_label`, because the mock matrix is keyed by those ids. Rate overlap is rejected in the service rather than with a GiST exclusion. A missing instruction gets one synthetic empty version so the assignment foreign key can be stored. SMS parsing, dashboard UI, payments, and a live beat stay in later milestones. AT-07, AT-15, and AT-16 are covered through the availability API, not an SMS parser. AT-32's dashboard UI is M6; M4 records uncovered reasons and exception rows.

## M5 — SMS dispatch and acknowledgment

Objective: safely send versioned schedules and record explicit acceptance/completion.

Dependencies: M4, inbox/outbox; live Twilio requires authorized credentials, sender registration and consent approval.

Tasks: recording mock first; parser/templates, long-message parts, consent/STOP, secure cleaner detail links, send-intent deduplication, delivery normalization, ambiguous-send reconciliation; acknowledgment versioning, +30/+60 timers; explicit unavailability and completion; stale-reply protection, retry expiry; verify Twilio SDK/signatures before optional production gateway.

Expected modules: `adapters/sms/{base,mock,twilio}`, `domain/sms_commands`, `services/{acknowledgments,cleaner_links}`, `workers/sms`, `api/webhooks`, synthetic callback tests.

Tests: AT-12–AT-16, AT-19, AT-26, AT-30, AT-31, AT-33, AT-34; verify no external send with flags off and opt-out race safety.

Definition of done: explicit acceptance distinct from delivery; replayed commands do not mutate twice; current-version/consent check occurs at send time; missing acceptance escalates without default reassign; mock tests complete before live sandbox trials.

Deliverables: complete mocked SMS lifecycle, verified sandbox contract evidence where available, operator escalation flow.

M5 scope boundary: `sms_messages`, `acknowledgments`, and `cleaner_access_links`, plus consent evidence columns on cleaners. The recorder is `RecordingSmsGateway`; `build_sms_gateway("twilio")` still refuses. Inbound and status routes accept the mock signature and a canonical `from` / `message_id` / `body` envelope. That envelope is not a verified Twilio signature scheme. The live `dispatch_tick` path still never sends. Mock recording is allowed only in test or development, with mock providers, empty credentials, production flags off, and an approved scenario policy. Reminder minutes come from that policy row. Missing acknowledgment opens an exception and does not reassign. STOP cancels queued mock messages and is not cleared by CONFIRM. Cleaner links store only a token hash, expire, and return the caller's current public assignment fields. A full backup restore and carrier sandbox trial are not claimed. Dashboard UI and payments stay later.

## M6 — Manager dashboard and exception handling

Objective: allow operators to supervise, override and reconcile safely.

Dependencies: M3–M5 APIs and auth.

Tasks: today/tomorrow timelines, availability, acknowledgment/completion badges, source freshness, SMS history, exception queue/ownership, instruction provenance/editor, overrides/locks and conflict reload UX; owner policy review/approval; secure personal-data views; independent alert channel configuration. Add prominent uncovered/late/stale-source states.

Expected modules: `frontend/app/{dashboard,properties,cleaners,schedules,exceptions,settings}`, `frontend/components`, `api/dashboard`, `services/exceptions`, Playwright fixtures.

Tests: AT-17, AT-18, AT-19, AT-23, AT-25, AT-29, AT-32, AT-34; role-specific navigation and server authorization; keyboard/form error flows.

Definition of done: every required dashboard view renders synthetic operational scenarios; stale edit yields recoverable conflict; manager cannot waive infeasibility; no sensitive access code in general UI/SMS logs.

Deliverables: tested manager UI, override audit trail, alerts runbook.

M6 scope boundary: `GET /v1/dashboard`, `GET /v1/exceptions`, and `PATCH /v1/exceptions/{id}` read the existing mock tables. No new migration. The UI pages are dashboard, exceptions, properties, cleaners, schedules, and settings. Payment preparation is not implemented, so the manager payment panel reports zero draft batches and says preparation is not enabled. Viewers do not receive `payment_queue_count`. Resolving `LOCK_CONFLICT`, missing duration, impossible deadlines, or uncovered routing codes returns 422. A stale `If-Match` returns 409. Settings cannot enable production dispatch. Instruction editing remains the existing versioned API; the properties page does not reveal access codes. Alert delivery stays on the operator outbox. No live SMS, schedule publication, or transfer is added.

## M7 — Payment preparation (historical, deferred)

Objective: prepare immutable, uniquely payable compensation for manual approval/export.

Dependencies: M3–M5 completed assignment facts, versioned rate setup, M6 manager UI.

Tasks: effective rate precedence and snapshots, eligible completion query, transactional batch/line uniqueness, totals, approval/void/adjustment controls, deterministic formula-safe CSV, reconciliation and approval queue. Do not create bank integration code or transfer credentials.

Expected modules: `domain/payments`, `services/{rates,payment_batches}`, `api/payments`, `frontend/app/payments`, migrations and race tests.

Tests: AT-20–AT-22, AT-23, AT-36 plus missing/zero rates, overlapping period concurrency and approved-line immutability.

Definition of done: duplicate imports/DONE/replans/preparation cannot duplicate liability; snapshots survive rate changes; approved CSV totals reconcile; no transfer capability.

Deliverables: rates UI/API, payment queue, approved exports and audit evidence.

M7 implementation: `services/payments.py`, `services/rates.py`, `api/routes/payments.py`, `frontend/components/PaymentManager.tsx`, `/payments`, and additive migration `0006_payments`. Financial writes serialize with a PostgreSQL transaction advisory lock before idempotency resolution; unique task-based earned-work keys and database triggers additionally protect history. Published rates are selected by cleaner/property/service date, with immutable versioned snapshots. Owner-only replacement closes an unused future portion of a rate and creates a new version; it rejects a boundary that would affect any published assignment. Approved snapshots remain unchanged. Void/reprepare retains the same line IDs; a voided correction moves on re-preparation of its exact original batch period. Overlapping new periods still cannot duplicate cleaning liability.

M7 tests: `tests/test_payments.py` covers AT-20/21/22/23/36 subsets (property precedence, rate-date boundaries, snapshot stability, concurrent preparation, idempotency, history/CSV, role denial, exclusions, zero rates, disputes and signed corrections). Playwright adds manager stale-review recovery, void/reprepare/approval/download, viewer denial and owner future-rate replacement. These are synthetic acceptance subsets, not evidence of bank payment, provider delivery or production launch readiness.

Local M7 evidence (2026-11-07): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app` passed; full `uv run pytest`: **117 passed**, one existing Starlette/httpx TestClient deprecation warning. This includes complete empty-database migration downgrade/upgrade and PostgreSQL constraints/concurrency. After adding exclusion auditing and a reassignment-rate regression, the final targeted `uv run pytest tests/test_payments.py` passed **14 tests**. `npm run typecheck`, `npm run lint`, `npx next build` passed; `npm run test:e2e`: **6 passed** against the isolated mock API and seeded fictional work. `npm audit --omit=dev`: **0 vulnerabilities**. The native Windows smoke tests now terminate their own process trees and use unique temporary logs. Python 3.12.13, Node 22.23.4, PostgreSQL 17 and Redis 8 were used locally; hosted PostgreSQL 16/Redis 7 CI must pass before merging. No external integration, carrier SMS, bank transfer, backup restore or production deployment was tested or enabled.

Hosted M7 verification subsequently passed on code commit `e378ad7`: **118 backend tests**, **6 browser tests**, backend lint/format/typecheck and frontend lint/typecheck on PostgreSQL 16/Redis 7. [PR CI run](https://github.com/tylerxia8/airbnb-management-bot/actions/runs/42401051115); the push CI run also passed. Three backend warnings were reported on Linux (existing dependency deprecations); no checks were skipped or failed. This evidence covers the mock milestone and does not clear the M8 gates below.

## M8 — Testing, pilot, and deployment

Objective: validate system behavior and operational readiness before controlled production activation.

Dependencies: M1–M7; resolved launch gates for selected source mode.

Tasks: run complete acceptance suite with Pytest/PostgreSQL/Playwright; performance and DST/race tests; secret/dependency scans; cloud/auth/backup configuration and restore drill; reconcile synthetic outage recovery; verify official production adapters with authorized sandbox/account tests; collect baseline, shadow schedules without live dispatch, review with manager, pilot small consented cohort, expand only after success review; kill switch and incident runbooks.

Expected modules: `backend/tests/{acceptance,integration,performance}`, `frontend/tests`, `infra/{staging,production}`, `docs/runbooks`, CI/deployment workflows and evidence reports.

Tests: AT-01–AT-36; direct checks for every FR, backup restoration with no stale SMS replay, pilot on-time and coordination metrics.

Definition of done: acceptance suite passes with exact evidence; unverified/disabled capabilities remain clearly labeled; manager approves policies, consent, source mode and pilot results; restore/kill switch demonstrated; owner enables production dispatch explicitly. Do not mark live deployment complete if approvals or external access remain unresolved.

Deliverables: release candidate, test/metric reports, signed launch decision, deployment/rollback/recovery instructions and honest remaining blockers.

Readiness review must audit inherited milestone limitations before treating their local tests as full PRD compliance. In particular, M4 uses a coverage model plus deterministic sequencing, with bounded joint CP-SAT routing for small plans, rather than the complete five-stage objective; same-day rolling/multi-day planning, manager override feasibility, and unchanged-cleaner version preservation require explicit contract review. M6's dashboard and exception queue do not prove every proposed editing workflow. Restore/RPO/RTO, managed auth, official provider verification, consent, cloud deployment, real business policy/rate approvals and pilot measurement remain unverified. Keep production startup/dispatch blocked throughout this review.

## Requirement traceability

| Requirements | Implementation | Acceptance coverage |
| --- | --- | --- |
| FR-01, FR-02 | M2 sync/adapters/inbox | AT-01, AT-02, AT-18, AT-26, AT-35 |
| FR-03 | M3 task derivation | AT-01, AT-04, AT-05, AT-27 |
| FR-04, FR-05 | M3 durations/windows; M4 validator | AT-03, AT-28 |
| FR-06, FR-07, FR-08 | M4 availability/origins/capacities | AT-07, AT-08, AT-09, AT-32 |
| FR-09, FR-10 | M4 routing/solver/preview | AT-09, AT-10, AT-11 |
| FR-11 | M4 publication/clock; M5 dispatch | AT-11, AT-24, AT-25 |
| FR-12 | M5 gateway/parser/delivery | AT-19, AT-26, AT-30, AT-31, AT-33 |
| FR-13 | M5 acceptance/timers | AT-12, AT-13, AT-31 |
| FR-14, FR-15 | M4 replan; M5 UNAVAILABLE | AT-07, AT-15, AT-16, AT-17 |
| FR-16, FR-17 | M2/M3 updates; M4 replan | AT-04, AT-05, AT-06, AT-27 |
| FR-18 | M5 completion service | AT-14, AT-16, AT-33 |
| FR-19 | M4 constraints; M6 overrides | AT-17, AT-25, AT-32 |
| FR-20 | M2/M3 instructions; M6 editor | AT-29, AT-34 |
| FR-21, FR-22 | M7 rate snapshots/batches | AT-20, AT-21, AT-22, AT-36 |
| FR-23 | M4/M5 exceptions; M6 queue | AT-10, AT-13, AT-18, AT-19, AT-32 |
| FR-24 | M1 audit; all write services | AT-17, AT-20, AT-23, AT-29 |
| FR-25 | M6 dashboard; M7 approval UI | AT-32 |
| FR-26 | M1 flags; M5 send gates; M8 launch | AT-30 |
| FR-27 | M1/M5/M6 security; M8 drills | AT-23, AT-26, AT-30, AT-34 |
| FR-28 | M8 guided setup, proxy account and dashboard refresh | test_setup.py; demo.spec.ts manager setup; dashboard.spec.ts refresh/stale-read subset |

M8 validates every row; it does not replace milestone-level automated checks.

Local demo verification in progress (2026-11-07): backend Ruff/format/mypy passed, and the five targeted demo/dashboard Pytests passed. Frontend typecheck and lint passed before the final browser corrections. The first full Pytest run reached 116 passed, but one source-encoding check failed and four setup errors followed disk exhaustion; the encoding was corrected. Initial browser attempts exposed a Next.js origin mismatch (fixed), a test selector ambiguity (fixed), and a local Next.js native-memory exit while C: was full. The demo now caps its V8 heap and disables persistent Webpack cache. Hosted CI must verify the final commit, including the separate isolated demo tour. These failed/interrupted runs are not passing acceptance evidence; production M8 gates remain open.

The final local demo browser tour subsequently passed (1 Playwright test, 8.2 seconds): manager sign-in, named dashboard, explicit confirmation, next-day preview/publication, recording-gateway messages and viewer control restrictions. Dashboard and schedule screenshots were rendered and inspected. The launcher successfully started its private services. Local C: disk exhaustion prevents claiming a clean full local regression/build; final hosted checks remain required. Reversible NTFS compression of this repository's generated Next.js build folder recovered some working space without deleting files.

After recovering local working space, the full final backend command set (`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app`, `uv run pytest`) passed: **122 tests**, one existing Starlette/httpx deprecation warning. `npm run typecheck` and `npm run lint` passed after presentation corrections. The demo browser tour and the separate exception-action/refresh regression each passed locally. Native Windows startup with a freshly seeded database succeeded; `--stop` and `--reset` are documented. CI additionally runs `npx next build`, the regular browser suite, and the isolated development-mode demo tour. Production M8 gates remain open.


M8 open-source evaluation (2026-11-07): remains **in progress**. Adopted the
existing OR-Tools optional-circuit/successor technique for plans up to 12 jobs
and 4 cleaners; retained the large-plan fallback and all production gates.
[Evaluation, measured tradeoffs and remaining gaps](OPEN_SOURCE_EVALUATION.md).
Acceptance coverage adds `tests/test_route_optimization.py` for AT-09/10/17
subsets, exhaustive small-instance correctness, break travel and preserved locks.
The full five-stage objective, publication of the exact persisted preview,
full override feasibility, rolling planning and production M8 gates remain open.


Open-source change validation (local, 2026-11-07): backend `ruff check .`,
`ruff format --check .` and `mypy app` passed. The full `python -m pytest
backend/tests` run passed **130 tests** with one existing Starlette/httpx
deprecation warning. That run began before the final fallback-selection hardening
and ninth regression; the final targeted `python -m pytest
backend/tests/test_route_optimization.py` subsequently passed **9 tests** with
the same warning, and Ruff passed again. Hosted CI on the final [PR #10](https://github.com/tylerxia8/airbnb-management-bot/pull/10)
head must verify the complete suite, frontend build and Playwright checks before
merge. Browser checks were not repeated locally for this backend-only change.
The seeded benchmark validated every result; measured quality/timing details and
untested production capabilities are recorded above. No checks were silently
skipped; production and full-objective acceptance remain unverified.


M8 manager experience (2026-11-07): user requested setup-and-supervise operation
and explicitly authorized a fictional Hospitable proxy because no account is
available. Added `/setup`, house creation/duration editing, cleaner creation/
eligibility/caps, dated workday editing with Eastern/DST validation, explicit
synthetic consent, a reservation timeline and a guarded server-side demo loop.
The loop runs every 30 seconds only after manager activation, publishes next-day
mock plans, records mock messages, preserves unchanged assignments and pauses
on failure/restart. Dashboard follows the server business date and refreshes
with explicit stale-read feedback. Added property cursor pagination to keep
new houses selectable beyond the seeded first 100. Row/advisory locks protect
property duration and competing dated-availability writes; no migrations changed.

FR-28 acceptance is a **local synthetic subset**, covered by
`tests/test_setup.py`, the OpenAPI surface regression and a second Playwright
manager walkthrough in `tests/demo.spec.ts`. [Manager guide](MANAGER_SETUP.md)
documents the flow, accelerated clock/cadence, configuration counts and remaining
recurring-calendar/editing/production gaps. M8 remains in progress; no production
launch, provider entitlement, wall-clock dispatch or durable recovery is claimed.


Local manager-flow evidence (2026-11-07): Ruff lint/format and mypy passed;
frontend TypeScript and ESLint passed. The full backend suite passed **140 tests**
with one existing Starlette/httpx deprecation warning after updating the OpenAPI
surface assertion for the added routes. Subsequent final targeted setup tests
passed **9 tests**, including Eastern reservation-window metadata and preservation
of explicit acceptance across a reconstructed demo app. The new manager browser
walkthrough and dashboard automatic-refresh/stale-read regression each passed.
The initial browser attempts exposed pre-hydration native form submission (fixed
by disabling house submission until loaded and using POST forms), an option/list
selector ambiguity (fixed), and an expectation shorter than the status polling
cadence (fixed with a bounded completion wait). A startup race with the existing
local Next.js listener was retried after the launcher reported ready. These failed
attempts are not passing acceptance evidence. The complete final local demo suite subsequently passed **2 tests** (22.4s),
and setup/dashboard screenshots were rendered and visually inspected. The hosted
production build/regular browser regressions must pass on the PR head before merge; no real provider, traffic, live SMS, transfer or production pilot
was exercised.

M8 presentation follow-up (2026-11-07): optional local public-record context supports the user's requested southeast Charlotte address examples. Source metadata and explicit historical-assessment/demo-assumption labels accompany the cards; real addresses stay in ignored local state. Default fixtures remain synthetic, the normal app has no housing-context endpoint, and manager authorization protects the demo-only endpoint. No milestone status or production readiness changes. Validation evidence is recorded in the follow-up PR.

Local follow-up validation: `pytest backend/tests/test_demo.py backend/tests/test_demo_housing.py -q` passed 5 tests (one existing Starlette/httpx warning); Ruff lint/format and `mypy app` from backend passed. Frontend TypeScript passed; the new housing Playwright regression passed (1 test, 4.8s), and a live screenshot confirmed 100 sourced cards. Full frontend ESLint completed successfully after an extended runtime; targeted ESLint also passed. Hosted CI provides the full-suite verification. Initial mypy invoked from the repository root missed backend configuration and reported an existing Celery typing issue; rerunning from backend passed. No production provider or real housing inspection was exercised.

M8 daily operations revision (2026-11-07), still in progress: manager requested a daily house/cleaner/deadline/completion board and evening next-day schedules. The authoritative PRD now caps no-arrival work at 48 elapsed hours after checkout, retains earlier buffered arrival deadlines, and allows only tighter manager targets. This intentionally replaces legacy indefinite-pending acceptance expectations. The dashboard includes carryover work and a persistent overdue queue, excludes historical assignments from counts, and exposes explicit cleaned facts. The guarded demo supports evening recording and labeled fixed-clock simulation; durable production scheduling and live provider launch gates remain open. No migration history is changed.

Daily operations validation: final targeted `pytest` over task-generation/scheduling/setup/dashboard/daily-operations/SMS modules passed **56 tests**, one existing dependency warning (80.62s). Ruff lint/format and mypy passed; frontend TypeScript and targeted ESLint passed. `playwright test tests/daily-cleaning.spec.ts` passed **1 test** (3.4s); live Charlotte dashboard screenshot inspected. Dedicated demo task recomputation preserved IDs/history; four selected-day and six next-day tasks were visible and assigned, one completed. Evening simulation recorded six new assignment parts plus two prior-version cancellation parts, with no external send.

The initial broad local backend run was interrupted after 51% while progressing slowly; no full local pass is claimed. An initial targeted run found an obsolete one-ready-task expectation after the 48-hour rule; it was corrected before the final 56-test run. Initial browser attempts found text encoding/selector expectation mismatch and a test-startup race while the local server was stopping; the final isolated browser check passed. Full production build and regression suite are required in hosted CI before merge. No live SMS, provider, production worker or pilot was exercised.

M8 saved driving baseline (2026-11-07), still in progress: one-time online OSRM capture for the 102 Charlotte examples plus the manager-selected public staging point. Runtime scheduling and independent validation reuse the directed local matrix without network calls or synthetic fallback; unknown legs remain unavailable. Snapshot/fingerprint provenance and per-row driving/source display are added. See [source evidence, capture rules and limits](DRIVING_BASELINE.md). No production provider, live traffic, durable worker or pilot is enabled.

Saved-baseline local verification: targeted `pytest` over driving-baseline/setup/daily-operations/route-optimization modules passed **28 tests**, one existing Starlette/httpx warning (29.09s). Ruff lint/format (including the capture utility), backend mypy, frontend TypeScript and targeted ESLint passed. The driving-aware daily-board Playwright test passed **1 test** (3.5s); live next-day table and attribution/staging screenshots were inspected. All six next-day tasks were assigned with cached-route snapshots, allowing 46 total rounded driving minutes across three cleaners. Re-running the capture utility confirmed the existing file was reused without online calls.

Initial checks found a missing forward-annotation import before any capture request, typing/format errors, and an existing database constraint requiring expiry strictly after observation. These were corrected; static snapshot expiry is capture + one microsecond, not renewed freshness. A frontend typecheck during launcher regeneration raced with removal of generated Next.js types; rerunning after startup passed. The first targeted backend run failed the expiry constraint; the final 28-test run passed. Full hosted CI/build/browser regression remains required before merge. Production traffic, road-access entrances, external SMS and provider launch were not verified or enabled.

## Scheduling-only scope revision (2026-11-09)

Manager request supersedes the M7 active feature scope: remove payments and Settings and focus on cleaner scheduling. Removed pages, navigation, dashboard finance fields, compensation onboarding, payment/rate APIs and calculation services. Finance implementation/tests are text archives under `docs/archive`; models, historical rows, immutability protections and applied migrations are retained. New additive migration `0007_scheduling_only` permits assignments without rates or payment amounts. Policy approval and other scheduling safety controls remain active.

Local validation: 57 targeted Pytest checks passed across scheduling, setup, dashboard, demo, SMS and route optimization. Ruff lint/format and mypy pass; frontend TypeScript and ESLint pass. The broader backend run, browser regressions and hosted CI are recorded in the scope-revision PR. M8 remains in progress; no live integration or dispatch is claimed.

## Hospitable-first manager workflow (2026-11-09)

Scope refinement within M8, following the revised PRD: one source screen for houses and reservations; three-step setup; four primary navigation links; manual scheduling/exception controls available from the dashboard; mock creation tools collapsed and house creation only shown in the guarded demo. Duration readiness uses effective approved defaults, so lack of an explicit override does not create a false setup task. Source bedroom information is read-only in the cleaning-time editor. Live Hospitable, recurring availability and other production gates remain open. Validation evidence is recorded in the associated PR; no milestone is newly marked complete.

Local validation for this refinement: `pytest tests/test_setup.py -o addopts= -q`: 10 passed; Playwright `tests/hospitable-setup.spec.ts tests/daily-cleaning.spec.ts`: 2 passed. Ruff lint/format, mypy and frontend TypeScript passed. Hosted CI and remaining frontend validation are recorded on the PR.

PR #16 follow-up: hosted CI started after retry and exposed a demo schedule-loading failure. The contextual dashboard link now uses client navigation, and schedule controls stay disabled until hydration attaches their handlers. Added cold/direct schedule-loading browser coverage and an explicit demo API-response assertion. This is an M8 workflow repair; milestone status remains in progress. Validation results are recorded on PR #16.

M8 entry-flow refinement (2026-11-09): one Connect to Hospitable button replaces the demo role picker. It opens the dashboard with a fixed manager session against the fictional account, with pending/error/connected states and explicit simulated labeling. The real connection stays unavailable. Development sign-in is collapsed outside the demo; automated permission coverage uses test sessions. Milestone remains in progress. Validation evidence is recorded in the associated PR.


M8 weekly setup and tomorrow-view refinement (2026-11-09), still in progress:
manager-confirmed recurring Eastern working hours with dated exceptions replace
repeated daily entry. Added migration 0008_weekly_hours preserves earlier history.
Weekly writes use optimistic versions, idempotency, audit and the publication
transaction lock; existing schedule-day revisions and fingerprints invalidate
stale previews. Explicit SMS unavailability persists a dated exception. The new
authenticated Tomorrow destination follows the server business clock, refreshes
and filters by cleaner; manual planning stays secondary. PRD and API/manager
contracts updated to remove the obsolete recurring-calendar gap. Live Hospitable,
durable automation and all other production/pilot gates remain open.
Local verification: 52 targeted Pytest checks passed across weekly/setup/scheduling/
SMS/health/dashboard/OpenAPI modules; a subsequent final weekly suite passed all
5 checks including SMS and manager UNAVAILABLE. Earlier migration/setup/weekly checks passed
16 tests, including migration round-trip. Ruff lint/format and mypy passed;
frontend TypeScript and targeted ESLint passed. The initial new browser run passed
source/daily-board checks but found a startup/selector timeout and an ambiguous
Next.js alert selector; after scoping selectors, both new workflow checks passed.
Final browser regressions passed 6 checks (weekly workflow, source setup, daily board and cold schedule loading); hosted full-suite evidence is recorded on the PR.
No production provider, carrier SMS, durable worker or pilot was exercised.

Visual verification: live tomorrow table and weekly-hours/staging selector rendered and inspected. The local demo was restarted without resetting its database, automation re-enabled, and all six next-day houses remained assigned. A text-encoding issue in a new navigation link was corrected after screenshot review. No addresses or screenshots were committed.


M8 cleaner-input follow-up (2026-11-09), still in progress: formatted phone entry,
hours-based capacity for new cleaners, and typed starting addresses replace the
fictional-only phone restriction, three required technical caps, and staging
selector. Migration 0009 preserves prior history and explicit legacy caps. Only
the guarded local saved-baseline demo captures address routes on manager save;
scheduler and independent validator reuse the stored directed cache, leaving
unknown legs uncovered. Address privacy, stale-version/idempotency/publication
locking and default-off production dispatch are retained. PRD/contracts and
backend/browser acceptance updated; validation evidence follows on the PR.


Local evidence: 65 targeted Pytest checks passed across cleaner-inputs, saved
routing, weekly hours, scheduling, SMS, migrations, health, setup and dashboard,
including migration round-trip (one existing dependency warning). Ruff lint/format,
mypy, TypeScript and targeted ESLint passed. Five Playwright checks passed across
cleaner forms, weekly hours, next-day viewer access, source setup and daily board.
The first new phone/capacity test used a paginated list that could omit its target
house; its fixture now selects the explicit synthetic house identity. Initial
static-check errors were corrected before final passes. Live rendering confirmed
normal phone entry and a typed starting address without submitting test records.
A one-time public staging-address capture returned ready with 102 destinations;
its cache and rendered screenshot remain ignored local artifacts. Hosted full
CI and merge evidence are on the PR. Production SMS/account access remain disabled.


M8 cleaner removal (2026-11-09), still in progress: manager-confirmed team removal
uses existing active/version fields, preserving historical rows without migration.
Transactional retirement releases unstarted unlocked assignments and flags locked
or possibly started work; completed history remains unchanged. Schedule-day and
cleaner fingerprint freshness, link revocation and inactive dispatch checks prevent
stale/new work going to former cleaners. Inbound command application and recorded
message dispatch share the publication lock. Active team list and manager/viewer
browser acceptance updated. Production launch gates remain open. Validation
commands/results are recorded on the PR after checks.


Local verification: 52 targeted Pytest checks passed across removal, scheduling,
SMS, weekly hours, setup and OpenAPI; the final removal suite passed all 4 tests,
including locked, possibly started and completed work. One existing dependency
warning remains. Five Playwright checks passed across removal permissions/
confirmation and existing cleaner forms/weekly/tomorrow workflows. Ruff lint/
format, mypy, frontend TypeScript and targeted ESLint passed. Initial formatting
errors were corrected before final checks. No real cleaner was removed from the
local demo during tests; tests use the separate test database. Hosted full CI
and merge evidence are recorded on the PR. No live SMS/integration was enabled.

M8 visual follow-up (2026-11-09), still in progress: manager-selected #e54a6f/#e76c8f rose theme replaces green branding across header, buttons, navigation, table surfaces and form accents. Dark text and darker rose links/focus preserve contrast; exception warning colors remain distinct. Validation: six targeted Playwright tests passed (daily-cleaning, weekly-workflow, remove-cleaner), and frontend TypeScript passed. Desktop dashboard visually inspected. Dark text contrast on the two brand colors measures 4.68:1 and 5.86:1. Mobile capture occurred during loading and is not evidence of a populated screen. Hosted CI evidence is recorded on the PR.

M8 Hospitable tab refinement (2026-11-10), still in progress: replaced the busy source screen with an Eastern date selector, five daily categories and one detail list, following [Hospitable’s documented Home dashboard](https://help.hospitable.com/en/articles/15505182-hospitable-home-dashboard). Connection/setup/import/demo tools and the house directory are collapsed. Local cleaning records are explicitly distinguished from unconnected Hospitable Tasks. The authenticated daily read has no 100-reservation table cap, handles local midnight boundaries, excludes canceled stays and retains dashboard carryover task semantics. Source freshness and incomplete import coverage remain disclosed. No upstream writes are enabled. Existing dashboard/ingestion/authorization checks passed: 21 tests, one dependency warning; backend Ruff lint/format and mypy passed. Frontend TypeScript/ESLint/production build passed. No tests were created or edited. Browser inspection was unavailable; visual parity is not claimed. The previously recorded full-suite regression failures remain unresolved, and M8 is not complete.

M8 Daily operations date navigation follow-up (2026-11-10): added accessible previous/next day buttons, retained the date picker and Today action, and kept navigation available through loading and read failures without showing records for the wrong day. Calendar-day arithmetic is independent of browser timezone/DST and respects server date bounds. Frontend TypeScript, ESLint and production build passed; local web redeployed. No tests were created or edited; no browser visual verification claimed. M8 remains in progress.

M8 cleaner self-service onboarding (2026-11-10), still in progress: added a manager-created, replaceable seven-day link and a separate cleaner page for recurring Eastern hours, one optional break per working day and encrypted starting address. Additive migration `0011_cleaner_onboarding` purpose-separates job/onboarding grants and adds cleaner-attributed audit; prior job tokens gain no write scope. Grant identity, expiry/revocation, rate limits, version checks, keyed idempotency and the publication lock protect saves. Cleaner saves perform no external address lookup, SMS or Hospitable mutation. Manager editing remains available in a disclosure. The exact onboarding proxy route forwards grant authentication independently of operator cookies; local host/origin restrictions remain. Hosting and actual onboarding message delivery are not enabled: localhost previews cannot be used from cleaners’ phones.

Validation: backend Ruff lint/format and mypy passed; frontend TypeScript, ESLint and production build passed. Existing weekly/setup/cleaner-input/removal/authorization/idempotency suites returned 33 passed and one failure (`test_removal_preserves_completed_work`: no assignment from the already recorded SystemClock/expired November 10 fixture). Existing migration suite passed all three checks. Each run reported the existing dependency warning. No tests or fixtures were created or edited; new onboarding browser/endpoint acceptance coverage is not claimed. Independent code review corrected operator-only idempotency auth and Docker-origin comparison before deployment. The local launcher backed up state/key, stopped old writers, upgraded and restarted the isolated workspace successfully. The regression gate and M8 remain open.

M8 onboarding simplification (2026-11-10), still in progress: replaced seven expanded daily editors with day selections and one usual workday, an optional break disclosure and a starting address. Day-specific hours remain available and automatically preserve mixed saved schedules. Inactive editor fields are disabled for validation/serialization; usual-time edits carry into the per-day editor and mode toggles retain drafts. Cleaner save shows confirmation with an edit action. Manager link copy is shorter while expiry/replacement and local-only sharing limits remain visible. Frontend TypeScript, ESLint and production build passed; no tests were created or edited, no backend changes, and no browser verification claimed. Earlier regression failures and hosting/messaging readiness remain open.

M8 onboarding presentation refinement (2026-11-10), still in progress: centered branded header and form card, restrained rose surfaces, clearer selected-day controls, larger time/address fields, optional-break panels, full-width save action and a refined confirmation state. Small screens use a four-column day grid. Styles are scoped to cleaner onboarding; save/authentication behavior is unchanged. Frontend TypeScript, ESLint and production build passed; no tests were created or edited. Browser preview host was unavailable, so visual verification is not claimed. Local web redeployed; earlier regression and production readiness gaps remain open.

M8 free local routing workaround (2026-11-11), still in progress: manager declined Google billing. Added private, pinned OSRM car/MLD routing over a buffered NC/SC OpenStreetMap extract and a resumable-by-completed-graph preparation utility. Source hashes, graph/profile identity, bbox and preparation timestamp stay in private manifests; old graph generations support rollback. Official Hospitable property coordinate fields were verified and are now encrypted locally only under the isolated opt-in. Free Census lookup requires one regional match; road matrices preserve directionality/nulls and reject excessive snaps, malformed responses and synthetic fallback. Baselines invalidate on changed/missing house or graph facts, and versioned publication-locked writeback preserves saved hours/address. Setup exposes current route coverage with source attribution. Scheduling, SMS and upstream mutation gates remain off.

Verification: final existing configuration/auth/idempotency/weekly/setup/cleaner-input/migration/reservation-ingestion suites passed 55 tests, with the existing dependency warning. Backend Ruff lint/format and mypy passed; new preparation/launcher Ruff checks passed; frontend TypeScript/ESLint/production build passed. No tests or fixtures were created or edited; full-suite pre-existing failures are not claimed resolved. Actual regional preprocessing and private startup succeeded. The running application reported 82 current houses ready and one cleaner ready; all 82 saved cleaner-to-house legs were reachable, with encrypted address and original seven-day hours preserved and capture audited. No personal data or raw external responses were committed. Browser verification and hosted production readiness remain open.

M8 Setup presentation refinement (2026-11-11), still in progress: the connected local Setup page uses three numbered cards with current cleaner/time/route counts and explicit readiness states, consistent action links, a compact source strip and a collapsed launch checklist. Mobile cards stack vertically. Status labels do not imply automation is enabled; the live routing privacy text now describes the disclosed Census/local OSRM flow. Frontend TypeScript, ESLint and production build passed; local web redeployed. No tests were created or edited and browser visual verification remains unavailable. Existing backend behavior and production gates are unchanged.

M8 Setup copy reduction (2026-11-11): removed introductory/card explanation paragraphs, shortened headings and actions, and show only actionable missing-item details. Counts, readiness, source status and collapsed launch/privacy/attribution details remain. Frontend TypeScript/ESLint/build passed; local web redeployed. No tests were created or edited; visual verification remains unavailable.

M8 manager-confirmed cleaning default (2026-11-11), still in progress: every house now uses 120 minutes unless an explicit positive house override exists, including future imports and five-plus-bedroom houses. Shared task derivation no longer reads bedroom maps; legacy policy fields remain accepted for compatibility. Completed tasks return unchanged. Additive migration0012 permits standard_default provenance and guards downgrade; no property overrides or history are bulk-rewritten. Setup displays2h default, house overrides are optional and clearing one uses the existing keyed/versioned patch contract.

Validation: Ruff lint/format, mypy, frontend TypeScript/ESLint/production build passed. Existing task-generation/setup/migration/idempotency/auth suites returned30 passed,3 failed and the dependency warning. Failures are test_without_approved_policy_caps_deadlines_but_blocks_missing_configuration (old missing-duration dispatchability expectation), test_duration_override_then_bedroom_default_and_missing_five_bedroom (old bedroom source/missing-five-bedroom expectation) and test_setup_only_requests_cleaning_times_missing_from_approved_defaults (old per-house missing-default count). No tests were created or edited under standing instructions; those contract assertions remain stale and the gate remains red. Local launcher backed up database/key, applied0012 and restarted successfully. The shared publication-locked recomputation confirmed82 houses with no explicit overrides and229 open tasks at120 minutes with standard_default provenance. Existing unrelated scheduling/SMS/policy blockers remain; M8 is not complete.

M8 Review locations navigation repair (2026-11-11), still in progress: Setup travel action now opens a dedicated Locations page instead of the generic cleaner editor. Manager-only read includes every active house and cleaner starting address with no100-row cap, client search, explicit per-house map links and separate house Mapped/cleaner Routes states. No geocoding or upstream calls occur on review. Independent review corrected the house label so binding presence is not presented as full route reachability. Backend lint/format/mypy, frontend TypeScript/ESLint/build and15 existing authorization/idempotency/migration checks passed (dependency warning). Live authorized read returned82 houses with82 pins and one saved cleaner starting address. No tests were created or edited; new-route/browser automated coverage is not claimed. Existing legacy contract failures and production gates remain open; local app redeployed.

M8 bedroom-based duration refinement (2026-11-11), still in progress: manager superseded flat two-hour estimates after cleaner guidance. Shared duration rule is60 +30×bedrooms minutes, with positive house overrides first; studio/other-size extrapolation was disclosed as provisional. Property reads supply effective and bedroom-default minutes; Setup labels the rule by size, and clearing an override uses the shared bedroom estimate. Source bedroom changes feed unfinished tasks automatically; completed history remains immutable and legacy120 metadata is compatibility-only. No migration needed because bedroom_default remains a permitted source.

Validation: backend Ruff lint/format/mypy and frontend TypeScript/ESLint/build passed; independent review found no defects. Existing task-generation/setup/migration/idempotency/auth suites returned30 passed,3 failed and the dependency warning. Remaining assertions require all tasks blocked without approved duration defaults, five-bedroom duration missing (now210), and all houses missing cleaning times before policy approval (now0). These are obsolete duration-contract expectations; no tests were created or edited under standing instructions, and the regression gate remains red. Local startup and shared publication-locked recomputation succeeded for82 houses;232 open tasks use bedroom_default with90/120/150/180/240-minute values reflecting the actual bedroom distribution. Scheduling/SMS/account mutation gates remain unchanged; M8 is not complete.

M8 dashboard focus/presentation revision (2026-11-11), still in progress: today’s cleaning board now puts house, actual cleaner/planned time, deadline and explicit status first. Compact open/assigned/unassigned/cleaned task counts, search, filters, Eastern date controls, refresh status and collapsed operational details replace the expanded multi-panel layout. Existing TaskList remains available for Tomorrow. Overdue work merges by task ID only on the server business date; blocked assignments, overdue deadlines and planned-finish conflicts remain visible. No assignments are generated and paused live scheduling is clearly labelled.

Validation: backend Ruff lint/format/mypy, frontend TypeScript/ESLint/production build and17 existing dashboard/auth/idempotency/migration checks passed, with the dependency warning. Independent review corrected the carryover date gate and Today→Next pending-refresh fallback. Live read returned28 selected-day tasks plus38 overdue records,54 unique tasks after deduplication, and zero named assignments; UI reflects actual Unassigned state. No tests were created or edited. Browser access host was unavailable, so visual verification is not claimed. Local app redeployed; prior legacy duration-test failures and production gates remain open.

M8 eight-person demo roster and SMS destination preparation (2026-11-11), still in progress: user clarified existing persona is fictional and authorized seven more fake names/numbers. Retained original ID/hours, labelled all eight demo, replaced contacts with reserved NANPA non-working numbers, and added editable weekly calendars with four part-time examples and a shared saved demo start. Live verification found eight calendars, eight ready route caches, zero opted-in recipients and zero outbound SMS rows. No fake consent or live sender was created. Verified source formatted addresses/units are encrypted locally for destination messages and manager review; all82 houses have destination addresses. Future rendering rejects missing destinations, and superseded-version wording no longer falsely removes current replacement work. Live send guards remain unchanged.

Official Twilio documentation was checked: new console users cannot create legacy test credentials; the current no-card30-day trial is limited to verified recipients and predefined content, so custom dispatch texts require the appropriate real account/sender path. Account creation/verification is still an external owner step; no live adapter/credentials/public webhooks/durable production workers were configured. Existing punt/claim120-minute escalation requirements remain unimplemented.

Verification: backend Ruff lint/format/mypy and frontend TypeScript/ESLint/build passed. Existing SMS/cleaner-input/reservation-ingestion/auth suite returned22 passed,12 failed and the dependency warning. Failures involve hardcoded November10/11 schedules with SystemClock producing no placed assignments/messages (known time-sensitive regression family); no guards were relaxed and no tests were created/edited. New destination rendering/browser acceptance coverage is not claimed. Local app redeployed; operational gate remains red and M8 is not complete.

M8 cleaning-time card refinement (2026-11-11): replaced the oversized By size label and repeated description with three compact server-driven bedroom/time example tiles. Duration rules are unchanged. TypeScript, ESLint and production build passed; local web redeployed. No tests were created or edited; browser visual verification remains unavailable.

M8 cleaning-time card follow-up (2026-11-11): user rejected the tiles; replaced them with plain aligned bedroom/time rows and readable hour/minute labels. TypeScript, ESLint and build passed; local web redeployed. No tests added or edited; visual preview unavailable.

M8 Setup card alignment (2026-11-11): cleaner/house counts now share the cleaning-time list’s minimum content height and flex alignment, with vertically centered metrics and aligned actions. Production build passed; local web redeployed. No tests added or edited; visual preview unavailable.

M8 header presentation refinement (2026-11-11): replaced the full pink header with a white bar, rose monogram, stacked brand text and compact source/status area. Removed duplicate local connection notices after sign-in while retaining visible read-only and scheduling/SMS-paused status. Connection controls remain available when signed out; mock/demo warnings and onboarding isolation remain. Added bounded timeouts to existing shell session requests. Frontend TypeScript, ESLint and production build passed; local web redeployed. No tests added or edited; browser visual verification unavailable.

Setup badge removal (2026-11-11): removed Local setup / Read-only Hospitable from the Setup heading and deleted unused styles. Production build passed; local web redeployed. Account permissions are unchanged.

Cleaners copy reduction (2026-11-11): removed the two introductory paragraphs and their Return to setup link. Production build passed; local web redeployed. No behavior changes.

Header copy removal (2026-11-11): removed the connected workspace’s Hospitable/read-only and scheduling/SMS-paused header block at user request. Mock/demo identification remains. Production build passed; local web redeployed. No integration or permission changes.

Interim email preparation (2026-11-11): added encrypted optional cleaner emails, manager contact editing, additive migration0013, private Gmail app-password setup and owner-only self-test to the sending address. Gmail credentials are not yet supplied; no email was sent and automatic assignment emails remain disabled. Connection/login has bounded retries and timeouts; message submission is never automatically retried, and durable idempotency reservation precedes SMTP. Backend Ruff check/format and mypy90files passed; integrated frontend production build/type/lint passed during local startup. Existing backed-up migration/startup completed successfully. No tests created or edited; existing known suite failures remain unresolved, and no milestone is marked complete.

Sample assignment email preview (2026-11-11): added owner preview and manual send for the unique active Benito demo profile using its saved email. The content prominently labels fictional work, a fictional address and tomorrow’s Eastern time window; no real job is created and automatic dispatch remains off. A fingerprint protects review freshness, and the send shares durable pre-submission idempotency with the existing self-test. The frontend uses one deterministic request key per preview and never automatically sends. Integrated Ruff/check/format,mypy90files and frontend production build/type/lint passed. No tests written or edited; existing suite failures remain unresolved. No sample assignment email was sent during implementation.

Local session recovery (2026-11-11): email status and other manager requests returned401 from the browser while the current local token succeeded. Shared API handling now signals401 to the shell, which reveals an explicit Reconnect control even when stale cookies claim a connected session. Reconnect returns to the current page; Gmail stops retrying authentication failures and explains recovery. Existing authentication remains enforced. Frontend build/type/lint and diff checks passed; local web redeployed. No tests added or edited.

Manual real assignment email preparation (2026-11-11): added date-based owner schedule listing, per-cleaner full previews and explicit manual sends from current published assignments. Source/contact/schedule/task/property fingerprints and row locks protect the reviewed content; durable idempotency prevents uncertain submissions from automatic retry. A separate local manual-email flag enables only these endpoints; scheduling and SMS remain paused. No current published schedules are fabricated and no real assignment emails were sent during implementation. Integrated Ruff/check/format,mypy90files and frontend production build/type/lint passed. No tests added or edited; previously reported suite failures remain unresolved.

Local manual scheduling preparation (2026-11-11): enabled isolated OSRM-backed previews/publication behind a separate local flag, added Schedules navigation/date controls, owner policy review/approval and task-deadline recomputation. Publication now validates/persists exactly the reviewed candidate instead of resolving the optimization; it rejects changed inputs and preserves all uncovered exceptions. Effective policy time is enforced with the supplied clock. No policy was approved, schedules published or messages sent during implementation. Ruff/check/format,mypy91files and frontend build/type/lint passed. Existing scheduling/baseline tests on disposable PostgreSQL/Redis:10passed18failed3warnings; failures show expired November10 fixtures producing no assignments and an outdated missing-duration expectation. Tests remain unchanged per owner instructions; no milestone marked complete and no gate weakened.

Local planning import blocker resolution (2026-11-11): verified official status-category schema and inspected only redacted status shapes. Seven never-imported not-accepted requests were incorrectly quarantined, preventing a successful current sync. Explicit untracked not-accepted records are now excluded without creating/canceling reservations or tasks; formerly tracked records and unknown facts stay blocked for review. Prior matching exceptions resolve only after a verified current read. Read-only permissions and all freshness checks remain. Ruff/check/format and mypy91files passed.

Automatic local email activation (2026-11-12): owner requested dashboard-first operation without manual planning/email review. Added explicit local automation flag,60-second supervised worker plus post-sync execution,6PM Eastern next-day scheduling,missed-night catch-up,once-per-date durable planning marker,and encrypted EmailDelivery outbox. Unique semantic HMAC keys prevent unchanged instructions from resending across ticks/restarts; queued claims commit before SMTP. Connection-only failures retry at most3times; uncertain submissions and abandoned claims do not automatically resend. Future changed scheduling inputs pause affected dispatch and raise review exceptions. Missing cleaner addresses are dashboard alerts. Existing approved policy was retained. Source polls now preserve unchanged assigned tasks; freshness requires the latest relevant source run to succeed,including after a newer partial/failed run. Independent validator now rejects overlapping visits to the same house.

Migration0014 adds the durable email table; the first controlled activation exposed missing application-role privileges before any email was submitted. Additive0015 grants onlySELECT/INSERT/UPDATE and preserves applied migration history. Backed-up migration/startup completed. First automatic live run published4assignments for the next service day; Gmail accepted1schedule email to the saved sample cleaner contact and3assigned cleaners were flagged for missing emails. No Hospitable account writes or SMS occurred. UI hides local planner/manual-email controls and shows status/problems on dashboard. Backend Ruff/check/format,mypy93files and frontend TypeScript/ESLint/production build passed. Existing scheduling/baseline suite is blocked at28setup errors because its unchanged reset fixture omits the new FK-referenced email_deliveries table; user forbids editing fixtures/tests, so no gate was bypassed or fixture modified. Previous18dated-fixture failures remain unresolved. No milestone marked complete.

Cleaner coordination and hosting preparation (2026-11-12): added private cleaner portal acceptance/completion/release/claim, durable120-minute offers and manageralerts,eligible peer emailnotifications, strict feasible insertion and prior-punter exclusion. Independent review caught and fixed late/unfinished travel assumptions,acceptance after changed facts,marker freshness laundering,and mixed-version email omissions. Main review also fixed per-house acceptance incorrectly inserting duplicate whole-version acknowledgments. Existing jobs/locks retain their IDs and times; direct cleaner actions are authenticated,idempotent,versioned and audited. No live job was accepted,completed or released during verification. Local backed-up migration0016/0017 deployment completed; Gmail accepted a new portal welcome email to the saved samplecontact and authenticated portal read returned200 with one ownjob and no offers. Backend Ruff/check/format and mypy95files passed; frontend TypeScript/ESLint/productionbuild passed. Caddy configuration and merged hostingCompose validated without public deployment. Demo roster retained per owner. The test-edit clarification received only the separate no-Hospitable-mutation constraint,not an explicit override of the standing no-test-edit instruction; tests/fixtures therefore remain unchanged and the previously reported reset-fixture block remains unresolved. No milestone markedcomplete.

GitHub/Railway synchronization (2026-11-12): preserved accumulated coordination changes and integrated newer origin/main scheduling safeguards,existing upstream test updates and documentation. Frontend conflict resolution retains local policy/date/OSRM/session behavior and remote mock assignment controls; local unsupported override buttons remain hidden. No test content was authored or manually changed. Added frontend build/start scripts honoring PORT and switched the existing frontend container command to npm start. Backend Ruff/check/format and mypy95files passed; integrated frontend TypeScript/ESLint/production build passed. Full Railway runtime remains unconfigured; no deployment or secret/data migration is claimed. Upstream override successor-travel/version-provenance gaps remain contained behind the local endpoint block and were not expanded into this synchronization task.
