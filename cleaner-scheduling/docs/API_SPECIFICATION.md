# Proposed internal application API

**Current scope (2026-11-09):** automate cleaner scheduling only. Payments, compensation setup and the standalone Settings screen are deferred. Their historical contracts and database records remain for possible restoration; they are not active features. Scheduling policies, hours, consent, eligibility, travel and deadlines remain because they determine feasible schedules.

All routes below belong to **our FastAPI application**, not Hospitable. No external provider routes are verified by this document. Prefix `/v1`; JSON UTF-8 except signed provider callbacks and CSV. OpenAPI is generated and checked against this contract during M1–M8 implementation.

## Common contract

UUID identifiers; RFC3339 timestamps with offset normalized to UTC; dates `YYYY-MM-DD` interpreted in America/New_York; integer cents and minutes. Reject unknown write fields. Null is accepted only for fields explicitly nullable. Responses use snake_case. Paginated reads use `{items:[Resource],next_cursor:string|null}` with opaque cursor, `limit` 1–100 (default 50). Detail responses include `id,version,created_at,updated_at` plus fields below. Never return credentials, full raw webhook payloads or access secrets through generic endpoints.

Operator routes require managed-auth bearer JWT with verified issuer/audience/signature/expiry; active roles resolved server-side: `V` viewer/manager/owner read, `M` manager/owner operations, `O` owner policy/rates, `S` restricted scheduler service identity. A cleaner has no general manager API token; SMS commands use validated provider ingress and registered sender binding. All write endpoints audit the actor.

Denied writes do not insert business rows, idempotency records, or outbox events. A denied write does append one sanitized `audit_logs` row with action `security.access_denied`, the actor when the token identifies one, a reason code, the route, and the status. That audit omits credentials, raw tokens, raw bodies, phone numbers, and access instructions. AT-23's "DB/outbox unchanged" means business tables and `outbox_events` stay unchanged; the security audit is required.

Role-aware serialization applies recursively to every resource and nested schedule/dashboard response: viewers never receive `amount_cents`, compensation snapshots, rate identifiers/versions, payment totals or sensitive instruction fields. Monetary fields shown in schemas below are M/O-only. Do not rely on the frontend to hide data already returned by the backend.

Non-webhook POST actions require `Idempotency-Key` (1–128 printable characters). Retain response/digest at least 72 hours (proposed); identical actor/route/key/body returns original result; different body →409. Domain uniqueness persists beyond retention. PATCH and version-sensitive actions require `If-Match: "<version>"` (428 if absent; 409 if stale). DELETE is not exposed for historical entities. Read operations are side-effect free and need no idempotency key. Service date filter cannot include unbounded ranges (proposed maximum 93 days).

Errors: `{error:{code,message,details,request_id}}`, redacted. Common expected errors: 400 malformed syntax; 401 missing/invalid JWT; 403 insufficient role/disabled feature; 404 unknown resource; 409 stale version/idempotency mismatch/state conflict; 422 field/domain validation; 429 rate limit; 503 unavailable dependencies. Endpoint-specific errors below add to these, not replace them. 202 returns `{job_id,status:"queued",status_url}`; `GET /v1/jobs/{job_id}` (M/S) returns `{id,state:queued|running|succeeded|failed,result_id?,error?}` and enforces job ownership/role. Jobs are durable and repeated requests return the same job.

## Health routes

M1 operations probes. They do not require an operator JWT and they do not call Hospitable, Twilio, or Google.

| Method / route | Auth | Response | Notes |
| --- | --- | --- | --- |
| GET `/health/live` | None | 200 `{status:"live"}` | Process liveness only. No database or provider calls. |
| GET `/health/ready` | None | 200 `{status:"ready",schema_revision:string}` | Database connectivity and the expected Alembic revision. 503 `dependency_unavailable` or `schema_mismatch` otherwise. |

## Resource schemas

Default eligibility (2026-11-10): POST `/cleaners` may omit `eligibility_property_ids`; omission defaults to all active current/future houses except optional `excluded_property_ids`. An explicitly supplied legacy allowlist retains its prior meaning, including an empty list. Nonempty exclusions cannot be combined with a legacy allowlist. GET `/cleaners/{id}/eligibility` is manager-only and returns cleaner version, eligibility policy and excluded active-property IDs. Idempotent POST to the same route accepts `{expected_version,excluded_property_ids}` and deliberately converts the cleaner to default-all with those exceptions. It validates active properties, preserves inactive-house exclusions, audits the change and invalidates previews transactionally. The local workspace permits this setup endpoint; no provider write or message is emitted.

Local read-only Hospitable mode (2026-11-10): the isolated workspace accepts authenticated, idempotent `POST /v1/reservation-syncs` with `{provider:"hospitable",mode:"full"}` and returns synchronous HTTP 200 `{job_id,status,status_url,rows_seen,rows_applied,upstream_writes:0,error_code}`. This preserves the existing mock 202 contract. Concurrent sync returns 409. `GET /v1/setup` reports `mode:"hospitable_read_only"` and `last_sync` with status, completion time and counts. Other mutations were initially blocked; the local setup extension below defines the current narrow allowlist. Frontend `/session` accepts the Hospitable connection action on the local host, using a separate private application token; it never accepts or returns the provider token. See the integration contract for discovery limits and production exclusions.

| Name | Fields beyond common identity/version |
| --- | --- |
| PropertyWrite / Property | `name:string(1..120), external_id:string, provider:string, bedrooms:int>=0, duration_override_minutes:int>0\|null, location: {address:string}\|{latitude:number,longitude:number}, active:bool`; response masks address unless M/O and includes geocode_status, not access codes |
| Reservation | `property_id:uuid,provider,external_id,status:active\|canceled,checkin_at,checkout_at,source_updated_at:null\|timestamp,timestamp_provenance,observed_at`; source edits only through ingestion |
| CleanerWrite / Cleaner | `display_name:string,phone_e164:string,active:bool,max_jobs:int>0,max_service_minutes:int>0,max_duty_minutes:int>0,default_work_intervals:[{start_local,end_local}],eligible_property_ids:[uuid]`; consent recorded separately with evidence; read masks phone except authorized operational view |
| Availability | `cleaner_id,service_date,status:available\|unavailable\|partial,work_intervals:[{start_at,end_at}],unavailable_intervals:[...],origin:{latitude,longitude}\|null,origin_source:submitted\|approved_default\|null,max_jobs_override:int>0\|null,max_service_override:int>0\|null,max_duty_override:int>0\|null,assumed:bool` |
| Task | `property_id,source_reservation_id,release_at,deadline_at:null\|timestamp,manager_target_at:null\|timestamp,duration_minutes:null\|positive int,status,completed_at:null\|timestamp`; null duration is blocked from scheduling with an exception |
| Assignment | `task_id,cleaner_id,service_date,planned_start,planned_end,status,is_locked,assignment_version,cleaner_schedule_version_id,acknowledged_at:null\|timestamp,completed_at:null\|timestamp,instruction_version,amount_cents,currency` |
| ScheduleRun | `service_date,status,input_fingerprint,input_revisions,solver_status,objective_values,assignments:[Assignment],uncovered:[{task_id,reason}],expires_at,version` |
| Exception | `code,severity:info\|warning\|critical,status:open\|acknowledged\|resolved,task_id:null\|uuid,assignment_id:null\|uuid,owner_subject:null\|string,details:redacted object,resolution_reason:null\|string` |
| PaymentBatch | `period_start,period_end,status:draft\|approved\|void,total_cents,currency:"USD",lines:[{id,task_id,assignment_id,cleaner_id,service_date,amount_cents,rate_snapshot}],approved_by:null\|string,approved_at:null\|timestamp` |

PATCH accepts any nonempty subset of allowed write fields, with full resulting entity validation. Foreign IDs must exist and be active where operationally required. Cross-resource consistency failures return 422, concurrency conflicts 409.

## Property and instruction endpoints

| Method / route | Auth | Request | Response | Validation / specific errors | Idempotency |
| --- | --- | --- | --- | --- | --- |
| GET `/properties` | V | query active?, cursor?, limit? | 200 page Property | valid filters | Read |
| GET `/properties/{id}` | V | path UUID | 200 Property | 404 missing | Read |
| POST `/properties` | M | PropertyWrite | 201 Property | unique provider/external_id; 409 duplicate; invalid coordinates 422 | Key |
| PATCH `/properties/{id}` | M | partial PropertyWrite + If-Match | 200 Property | deactivation with future work opens exception; stale 409 | Version |
| GET `/properties/{id}/instructions` | M | path UUID | 200 `{property_id,instruction_version,source_fields,local_overrides,effective_fields,provenance}` | sensitive fields restricted/audited | Read |
| POST `/properties/{id}/instructions/versions` | M | `{expected_version:int,local_overrides:object,remove_override_fields:[string],reason:string}` | 201 instruction version as above | reject overlapping set/remove, blank reason, unknown fields; 409 changed version | Key + version in body |

## Reservation integration endpoints

| Method / route | Auth | Request | Response | Validation / specific errors | Idempotency |
| --- | --- | --- | --- | --- | --- |
| GET `/reservations` | V | property_id?, from?, to?, status?, cursor? | 200 page Reservation | bounded interval | Read |
| POST `/reservation-syncs` | M/S | `{provider:mock\|api\|calendar,mode:incremental\|full}` | 202 job | 403 production disabled; 422 unsupported capability; 409 already-running returns existing job link | Key |
| GET `/reservation-syncs/{id}` | V | path UUID | 200 `{id,provider,status,started_at,finished_at,source_as_of,snapshot_complete,rows_seen,rows_applied,error_code}` | 404 missing | Read |
| POST `/reservation-imports/preview` | M | multipart CSV file per integration doc | 201 `{import_id,digest,valid_rows,errors:[{row,code}],expires_at}` | 413 size>5 MB; 422 malformed schema; partial errors block commit | Key |
| POST `/reservation-imports/{id}/commit` | M | `{expected_digest:string,source_order_confirmed:bool}` | 202 job | 409 digest changed/expired; 422 unconfirmed ordering or invalid rows | Key |
| POST `/webhooks/hospitable` | Verified provider | raw body and documented headers, **wire schema pending verification** | 202 `{event_id,status:"accepted"}` after inbox commit; duplicate returns same ID | 403 disabled/unverified capability; 401 failed validation; 413 payload limit; 503 cannot persist | Durable inbox key, no operator header |

## Cleaner and availability endpoints

| Method / route | Auth | Request | Response | Validation / specific errors | Idempotency |
| --- | --- | --- | --- | --- | --- |
| GET `/cleaners` | V | active?, cursor? | 200 page Cleaner | mask personal data | Read |
| GET `/cleaners/{id}` | V | UUID | 200 Cleaner | 404 missing | Read |
| POST `/cleaners` | M | CleanerWrite | 201 Cleaner | phone E.164, unique active phone, eligibility IDs; consent starts pending | Key |
| PATCH `/cleaners/{id}` | M | partial CleanerWrite + If-Match | 200 Cleaner | cannot clear opt-out here; 409 duplicate phone/stale version | Version |
| POST `/cleaners/{id}/consent` | M | `{status:opted_in\|opted_out,evidence_ref:string,occurred_at:timestamp,reason:string}` | 201 `{cleaner_id,status,version}` | fresh evidence needed for opt-in; no general API clears STOP without proof | Key |
| GET `/availability` | V | service_date required, cleaner_id? | 200 page Availability | business date | Read |
| POST `/cleaners/{id}/availability` | M | Availability excluding cleaner_id + `{expected_version:int\|null}` | 200 Availability (201 first creation) | unique date, origin valid, intervals non-overlapping; unavailable ignores capacity; changed version 409 | Key + expected version |

## Scheduling, assignment and task endpoints

| Method / route | Auth | Request | Response | Validation / specific errors | Idempotency |
| --- | --- | --- | --- | --- | --- |
| POST `/schedules/previews` | M/S | `{service_date:date}` | 202 job → ScheduleRun | 422 missing policy/duration/location represented as uncovered reasons; 503 solver unavailable; never sends SMS | Key |
| GET `/schedules/runs/{id}` | V | UUID | 200 ScheduleRun | private internal snapshots excluded | Read |
| GET `/schedules` | V | service_date required | 200 `{service_date,revision,active_run:null\|ScheduleRun}` | empty date returns null active_run | Read |
| POST `/schedules/runs/{id}/publish` | M/S | `{expected_fingerprint:string,expected_day_revision:int}` + If-Match run version | 200 `{schedule_run_id,day_revision,dispatch_intent_ids:[uuid],uncovered:[...]}` | 409 stale preview; 422 validator/lock conflict; 403 policy not approved; production sending separately gated | Key + version |
| POST `/assignments/{id}/override` | M | `{cleaner_id:uuid,planned_start:timestamp,is_locked:bool,reason:string}` + If-Match | 200 `{assignment:Assignment,day_revision:int,new_schedule_version:uuid}` | derive end from duration; 422 infeasible/empty reason; 409 locked unless explicit unlock step first | Key + version |
| POST `/assignments/{id}/lock` | M | `{locked:bool,reason:string}` + If-Match | 200 Assignment | cannot lock canceled/completed change; 409 stale | Key + version |
| GET `/tasks` | V | service_date? or status?, bounded from/to?, cursor? | 200 page Task | includes pending_deadline filter | Read |
| POST `/tasks/{id}/target` | M | `{manager_target_at:timestamp\|null,reason:string}` + If-Match task version | 200 Task | only no-known-arrival tasks; target>release; 409 source deadline now exists | Key + version |
| POST `/tasks/{id}/complete` | M | `{assignment_id:uuid,assignment_version:int,completed_at:timestamp,reason:string}` + If-Match task version | 200 `{task:Task,assignment:Assignment}` | future completion beyond clock tolerance (proposed 2 min) 422; wrong/stale assignment 409; duplicate same completion returns current state | Key + version, domain dedup |

## Exceptions and dashboard

| Method / route | Auth | Request | Response | Validation / specific errors | Idempotency |
| --- | --- | --- | --- | --- | --- |
| GET `/exceptions` | V | status?, severity?, cursor? | 200 page Exception | valid enums | Read |
| PATCH `/exceptions/{id}` | M | `{status?,owner_subject?,resolution_reason?}` + If-Match | 200 Exception | resolve requires reason and underlying-condition check; cannot waive deadline feasibility; 422 unresolved cause | Version |
| GET `/dashboard` | V | service_date required | 200 `{today,tomorrow,availability,assignment_counts,ack_counts,completion_counts,exceptions,sync_health,sms_delivery_counts}` | role-based operational redaction | Read |
| GET `/sms-messages` | M | cleaner_id?, service_date?, status?, cursor? | 200 page `{id,cleaner_id,purpose,status,accepted_at,error_code,schedule_version,redacted_preview}` | no access codes/full raw callback | Read |
| POST `/policy-versions` | O | `{settings:object,reason:string}` | 201 policy version including `id,version,created_at,updated_at,version_number,settings,approved_at:null,approved_by:null,effective_at:null,is_active:false` | schema/time/range validation; cannot enable transfers, missing-ack reassignment, or mandatory return-to-base | Key |
| POST `/policy-versions/{id}/approve` | O | `{approval_note:string,effective_at:timestamp}` + If-Match | 200 policy version with approval fields | Approving settings does not enable dispatch, SMS, or transfers. M1 has no production adapter, so launch remains blocked even after this row is active. Activation atomically supersedes the prior active policy. 422 when settings try to enable a gated capability. | Key + version |

## Twilio ingress contract details

| Method / route | Auth | Request | Response | Validation / specific errors | Idempotency |
| --- | --- | --- | --- | --- | --- |
| POST `/webhooks/twilio/inbound` | Verified Twilio signature | provider-documented form encoding; adapter extracts message identity, sender, recipient, body | 200 empty valid TwiML `<Response/>` after durable inbox; reply sent through outbox | Verify using supported SDK/current docs and externally visible URL; 401 invalid signature; 413 oversized; 503 inbox unavailable | unique provider message identity; no operator key |
| POST `/webhooks/twilio/status` | Verified Twilio signature | provider-documented status callback normalized to message identity/status/error | 204 after durable status-event storage | reject unknown binding without state mutation (quarantine); 401 invalid signature; 503 persistence unavailable | provider event identity or stable callback digest; monotonic updates |

Twilio field names, signatures, messaging service options, opt-out callbacks and status mappings must be verified against current official docs/SDK during M5; canonical schemas above are application contracts. Ingress accepts callback regardless of operator JWT but requires provider verification and rate limiting. Do not echo sensitive instructions in replies. Provider ingress limits proposed 256 KB; exact limits must accommodate verified payloads.


## Isolated local demonstration contract (M8 subset)

Only `app.demo.server:factory` mounts `/v1/demo/*`; `app.main` returns 404 for these routes. The factory enforces explicit local mode, all mock providers, disabled production flags, no provider credentials, dedicated loopback `cleaning_demo` application/migration targets, and Redis database 7. Requests from non-loopback clients are denied. The business clock is frozen at November 10, 2026 18:00 UTC; JWT expiration uses actual time. See [local demo](LOCAL_DEMO.md) for isolation and launch details.

- `POST /v1/demo/session` accepts `role` (manager/owner/viewer), and issues an eight-hour token for that registered fictional operator. It is deliberately unauthenticated inside the loopback-only demo. Next.js exchanges this server-side into its HttpOnly session cookie; the mock signing secret never reaches the browser.
- `GET /v1/demo/assignments` requires manager/owner and lists current fictional assignment IDs, task IDs, cleaner display names, service dates and statuses.
- `POST /v1/demo/record-sms` requires manager/owner and runs the existing intent deduplication and recording gateway. Repeated requests do not create duplicate schedule intents. It never calls a carrier.
- `POST /v1/demo/reply` requires manager/owner, accepts assignment_id, command (CONFIRM/DONE/UNAVAILABLE) and UUID event_id, and invokes the existing command handler with the associated fictional cleaner. It checks current schedule version and uses transactional serialization/idempotency. A reused event ID with another payload returns 409. Domain rejection remains a processed message, not a successful completion; inspect updated assignment state and mock reply history.

Dashboard task rows additionally contain property_name, cleaner_name, planned_start, planned_end and acknowledged. These contain public operational facts only; existing viewer financial redaction remains in force. The schedule UI calls the existing preview/publication contract with its fingerprint, day revision, If-Match version and idempotency key; no feasibility guard is relaxed.


## M8 guided setup additions

- `GET /setup` (V) accepts optional Eastern `service_date`; defaults to the server
  business clock. Also supplies a 31-day reservation window bounded by Eastern
  local midnights, serialized in UTC (including DST). Returns mock-mode configuration counts, policy/source indicators,
  and production blockers. `live_automation_ready` is always false in this release.
  Presence of any effective compensation rule does not imply all assignments have
  a matching rate. No phone, address, secret, evidence text or rate amount is returned.
- `GET /cleaners/{id}/availability?service_date=...` (M) returns the dated version,
  status, intervals and synthetic origin. Falls back to confirmed weekly hours when no dated record exists; source=weekly has dated version zero so a dated exception can be created.
- `POST /cleaners/{id}/workday` (M, Idempotency-Key) accepts `service_date`, local
  `start_time/end_time`, optional paired `break_start/break_end`, `origin_label`
  and `expected_version`. Converts America/New_York to UTC; rejects reversed hours,
  out-of-shift breaks, ambiguous/nonexistent DST instants and stale writes. One
  date/shift/break only, not a weekly recurrence. Audit omits origin/personal data.
- `GET /properties` now returns a real `next_cursor` when another page exists.
  Pass it as `cursor`; limit remains 1-100. Cursor uses a property UUID anchor and
  stable `(created_at,id)` ordering. Viewer location redaction applies on every page.
- `GET /dashboard` now accepts an omitted date and uses the server Eastern day.

The explicitly guarded demo factory alone mounts `GET /demo/account` (V),
`POST /demo/automation` (M, desired enabled state) and
`POST /demo/turnovers` (M, Idempotency-Key). The account is fictional and states
that no external account was created. The desired-state control is naturally
repeatable and audited; it is not a production policy/dispatch switch. Turnovers
accept a mock `property_id`, `checkout_date`, Eastern `checkout_time` and
`next_checkin_time`; they add a synthetic departure and next arrival via canonical
ingestion. A global demo transaction lock serializes turnover ingestion and
background cycles before idempotency resolution. The loop is initially paused,
checks every 30 real seconds, plans only the next Eastern business date, and
stops after failure/restart. No guest data or external provider protocol exists.

## Daily cleaning operations revision (2026-11-07)

`GET /dashboard` today/tomorrow now group current assignments by service date and unassigned work across its checkout-to-deadline window, not just checkout date. Canceled work is excluded. Rows add `assignment_id`, `assignment_status`, `scheduled_date`, `completed_at`, `cleaned`, `overdue`, `needs_assignment`; `overdue` at top level lists all overdue open tasks regardless of selected date. `unassigned_count` covers the selected day. Counts exclude superseded/canceled assignment history. Deadlines apply checkout + 48 elapsed hours even to legacy rows; earlier manager targets/readiness deadlines win. `POST /tasks/{id}/target` accepts only earlier targets, clearing restores the hard deadline.

Guarded demo-only `POST /demo/evening` (manager/owner) prepares the next-day plan and records schedules under a simulated 18:00 Eastern send clock. Repeated calls do not duplicate version/part message intents. Normal application returns 404; viewers receive 403. `/demo/account` exposes `last_evening_dispatch`; scheduled records contain house name, Eastern service start/end and deadline plus CONFIRM/DONE commands. Current local loop gates automatic recording at/after 18:00 business time. The frozen business date never advances itself; production SMS/clock workers remain disabled.

Saved demo driving context: dashboard task rows add `travel_before_seconds`, `travel_from`, and `travel_source` (`osrm_cached` or the original snapshot provider); `daily_travel_seconds` sums current selected-day assignments. `routing` provides a safe summary (capture date, source, point/pair counts, assumed public staging, traffic=none), also present in `/demo/account`. No coordinates, route matrix, private origins or new production endpoints are exposed. Snapshot provenance and a routing-baseline revision enter preview/freshness checks. Historical assignments retain their original route source.

### Condensed setup readiness

`GET /v1/setup` now includes `properties_needing_cleaning_time`: active properties with neither a positive override nor a valid bedroom default from the active approved policy. The older `properties_without_explicit_duration` count remains compatible but is not a manager action count. Source information remains read-only in normal onboarding; no live Hospitable capability is implied.

### Simulated connection entry

Frontend `POST /session` accepts `{connection:"hospitable"}` only with `LOCAL_DEMO=1`. The session role is fixed to manager regardless of a supplied role. It uses the existing guarded `/v1/demo/session`, sets the application token in an HttpOnly cookie and the presentation role cookie, then the client opens `/dashboard`. A different Origin is rejected with 403; outside demo mode this request returns 503 without cookies. Existing development-token and test-role flows remain development tools, with their selector absent from the demo UI. No upstream Hospitable endpoint is introduced.


### Recurring cleaner hours (M8 workflow refinement)

`GET /cleaners/{id}/weekly-hours` (manager) returns `{version, days}`.
`POST` on the same path requires manager authorization and Idempotency-Key,
`expected_version` (zero initially), and at most seven distinct weekday rules.
Weekdays are Monday=0 through Sunday=6. Each rule contains local
`start_time`, `end_time`, optional paired `break_start/break_end` and a synthetic
`origin_label`. Empty days clears recurrence. Unselected weekdays are days off.
Hours must be ordered and breaks must fit the shift. No overnight shifts.
Weekly edits share the publication lock, bump existing schedule-day revisions,
and are included in fingerprints. Stale writes return 409; audits omit hours/origins.
No dated records are generated in advance: explicit dated records (including
unavailable) always take priority. Weekly hours resolve in America/New_York per
service date; ambiguous or nonexistent DST times provide no availability, leaving
work uncovered for review. SMS UNAVAILABLE materializes an explicit dated exception
and uses the existing transactional replan and lock-conflict handling.
GET /setup includes tomorrow_date and tomorrow_cleaners_without_workday.
The authenticated /tomorrow UI reads /dashboard, refreshing every 30 seconds;
viewers see published allocations and uncovered houses without write controls.


Cleaner-input revision (2026-11-09): POST /cleaners accepts normal formatted
phone numbers, normalized to E.164 before encrypted storage and duplicate checks.
The three capacity fields are optional/null for new hours-based cleaners. Legacy
explicit limits still work; absent limits use the dated/weekly work interval,
with existing break, travel, deadline, independent-validation and lock constraints.
Weekly-hours POST accepts optional top-level starting_address (5-300 characters)
instead of per-day origin_label. GET returns the decrypted address to managers
only. POST responses/idempotency records/audits omit the address; GET includes
route_status. Starting addresses are stored as development ciphertext; managed
production encryption is still a launch gate. A successful save may report
routing_not_configured, address_not_found, routing_unavailable,
outside_demo_area or location_needs_review. The UI must show this explicitly.
The guarded local saved-baseline mode alone captures outbound routes at manager
save; identical resolved addresses/baselines reuse the cache. Scheduler/validator
only read saved durations. No saved baseline means no geocoder/router calls and
no synthetic fallback for an address-backed origin. Snapshot travel provenance
and business-date/version checks remain in force. Migration 0009 is additive;
downgrade refuses to invent mandatory limits for hours-based cleaners.


### Remove a cleaner (M8)

POST /cleaners/{id}/remove requires manager authority, Idempotency-Key and
{expected_version}. It shares the publication transaction lock and row-locks the
cleaner/affected assignments. Unknown id returns 404, stale version 409, viewer
403. Returns {active:false,released,needs_review}. Sequential same-key retries
replay the result. GET /cleaners lists active team members only; historical joins
retain the inactive cleaner identity. No records are deleted and no migration is
needed. Future unlocked open assignments become superseded and their tasks become
ready; CLEANER_REMOVED exceptions keep uncovered work visible. Locked and possibly
started assignments become blocked without changing owner, times or lock, with
LOCK_CONFLICT/IN_PROGRESS exceptions. Completed work is unchanged. Current cleaner
schedule versions are superseded and access links revoked; new link issuance and
queued dispatch reject inactive cleaners. Cleaner versions/activity participate in
fingerprints and all existing schedule-day revisions are bumped. Inbound command
application and mock dispatch share the publication lock to serialize retirement
with completion/acceptance/dispatch. Connecting or removing never enables live SMS.
The UI confirms removal locally and explains preserved history and review counts.

Local setup extension (2026-11-10): the Hospitable read-only workspace now permits authenticated POST `/cleaners`, `/cleaners/{id}/weekly-hours`, `/cleaners/{id}/remove`, and PATCH `/properties/{id}` in addition to source sync. All other mutations stay blocked. This changes only the local application allowlist; upstream access remains GET-only. Weekly address saves return `routing_not_configured` without an external lookup. Property PATCH in this workspace requires Idempotency-Key and If-Match and rejects source-owned bedrooms; it stores only a local duration override. The existing mock PATCH wire contract remains compatible. Cleaner names are trimmed, duplicate eligibility IDs are rejected, inactive properties cannot receive new eligibility, and real setup request digests are keyed. Existing role/version/audit checks remain in force.

## Daily operations read (2026-11-10)

GET `/v1/daily-operations` requires reader authorization and accepts optional `service_date` (Eastern, default today, seven days back through ninety days ahead; out-of-range returns 422). Returns business/service dates, allowed date bounds, occupancy reference time, active house names, occupied stays, check-ins, check-outs, same-day turnover transitions, local cleaning tasks, source freshness and workspace mode. Booking groups use canonical active reservations without the paginated reservation table limit; each local midnight is converted separately to UTC. Today occupancy uses the current instant; other dates use start of day. Local tasks retain dashboard carryover/current-status semantics and are not imported Hospitable Tasks or historical snapshots. This endpoint reads local data and never contacts or changes Hospitable.

## Cleaner self-service onboarding (2026-11-10)

Manager/owner POST `/v1/cleaners/{id}/onboarding-link` requires Idempotency-Key and accepts `{ttl_hours:1..168}` (default 168). Returns 201 `{grant_id,expires_at,sms_sent:false,token}`; each fresh issuance revokes prior onboarding grants for that cleaner. Replays return the same active grant/token; expired/revoked grants return 409 and cannot be revived. Only token hashes are stored; response replay reconstructs the secret from grant identity and the private field key. Existing job links retain job-only scope.

GET and POST `/v1/cleaner-onboarding` use that grant as Authorization Bearer, never an operator cookie. GET returns the cleaner’s own weekly-hours record plus display name. POST accepts the weekly-hours body, requires an explicit starting_address and Idempotency-Key, rejects synthetic origin labels, and derives cleaner identity exclusively from the grant. Expired/replaced/revoked/inactive-cleaner grants return 401. Public and per-cleaner rate limits apply. Saves share Eastern hour/break validation, expected_version conflicts, publication locking, preview invalidation, encrypted address storage and cleaner-attributed audit; addresses are not looked up externally. Sensitive request digests are keyed. The frontend link uses a URL fragment so its token is excluded from server request paths and query logs; exact onboarding proxy requests forward this bearer and ignore manager cookies. No SMS, Hospitable write, or consent change occurs.

Free local routing extension (2026-11-11): GET `/v1/setup` adds `local_routing:{configured,houses_ready,cleaners_ready,traffic:"none"}`. House readiness requires current source coordinates and graph bindings; cleaner readiness requires a complete saved matrix matching that baseline. Weekly-hours/onboarding reads add `routing_mode:local_osrm|unconfigured`. Enabling LOCAL_FREE_ROUTING in the isolated account permits the disclosed Census address lookup and private OSRM route capture, superseding the previous preparation-only lookup restriction. Complete successful unchanged-address caches are reused; partial, ambiguous, distant-road and unavailable routes remain explicit review states. Background capture preserves hours/address, checks optimistic versions/active cleaner status and publication lock before writing, increments dependent revisions and audits only capture status/count. All other onboarding scope, idempotency, role and scheduling/SMS gates remain unchanged.

Cleaning default refinement (2026-11-11): every house has effective duration 120 minutes unless a positive duration_override_minutes exists. The legacy policy bedroom map remains accepted but no longer selects duration. GET `/v1/setup` adds default_cleaning_minutes:120 and reports no missing house-duration setup work. Existing PATCH `/v1/properties/{id}` with clear_duration_override:true restores that default, with existing authorization/version/idempotency rules. New/revised open tasks use duration_source standard_default or property_override; historical bedroom_default/missing values remain readable, and completed tasks retain their recorded facts.

Locations review (2026-11-11): manager/owner GET `/v1/locations` returns all active houses and active cleaner starting-location records, without the paginated property-list cap. House rows contain local ID/name, source latitude/longitude or null and mapped/needs_review status. Cleaner rows contain local ID/name, decrypted saved starting_address and current ready/needs_review/missing_address status. No phones, tokens or access instructions are returned. Existing manager authentication/rate limits/no-store headers apply; viewers and cleaner grants have no access. This read performs no geocode, provider call or mutation. House Mapped indicates a current baseline binding, not guaranteed reachability of every matrix leg. The frontend opens individual OpenStreetMap links only after explicit clicks.

Bedroom duration refinement (2026-11-11): the uniform default is superseded by 60 + 30 × bedrooms minutes; positive property overrides win. GET property serialization adds cleaning_duration_minutes (effective), default_cleaning_duration_minutes (bedroom estimate) and duration_source. GET `/v1/setup` adds cleaning_minutes_by_bedrooms examples 0..5; legacy default_cleaning_minutes:120 is compatibility-only and must not be displayed as the active universal default. Existing patch clearing restores the bedroom estimate. Tasks use existing bedroom_default provenance; prior standard_default history remains readable and completed tasks are unchanged. No schema migration is needed.

Dashboard presentation support (2026-11-11): GET `/v1/dashboard` adds business_date (current Eastern business date), mode (mock or hospitable_read_only) and duration_minutes on task summaries. Existing today/tomorrow/global overdue arrays, assignment identity/status/acceptance/completion and freshness semantics remain unchanged. The UI merges today with global overdue only when the selected service_date equals business_date and deduplicates by task ID. Summary totals count cleaning tasks, not unique house identities. Historical/future selections do not receive current-day carryover. Reads do not allocate or publish assignments.

SMS destination preparation (2026-11-11): manager-only GET `/v1/locations` house rows now also include source formatted address and unit (nullable), decrypted only for that authorized response. Generic property/viewer payloads still omit these fields. The verified source schema defines address.number as apartment/unit. Future assignment-body rendering for Hospitable properties requires a nonempty formatted destination and preserves source unit text; no send gateway or dispatch permission is added. Address-only source records remain geocode_status unverified.

Local email setup (2026-11-11): cleaner create accepts optional email; cleaner summaries expose email_stored only. Manager GET /v1/cleaners/{id}/email returns the decrypted contact and version; POST to that route accepts email and expected_version with Idempotency-Key, including blank/null to clear. Owner GET /v1/email/status reports private Gmail configuration and self-test availability without revealing credentials. Owner POST /v1/email/test sends a fixed connection message only to the configured sender, reserves idempotency before SMTP, records the result, and never retries a message submission. Unknown delivery requires inbox review. Automatic assignment emails remain disabled.

Sample assignment email (2026-11-11): owner GET /v1/email/sample-assignment previews a clearly fictional house, tomorrow in America/New_York, an11AM–1PM Eastern cleaning window,2hours and Benito Johnson (Demo)’s decrypted saved email. It returns cleaner_id,cleaner_version,recipient,subject,body,fingerprint. Owner POST to the same route accepts only fingerprint and requires Idempotency-Key. The first request validates the current contact/version/date/body; duplicate keys replay before revalidation and do not resubmit SMTP. No tasks or assignments are created. Contact details remain out of stored results/audits.

Manual published assignment emails (2026-11-11): owner GET /v1/email/assignments accepts optional service_date(default Eastern business date) and returns business_date,service_date,items with cleaner_id,cleaner_name,email_stored,assignment_count. It lists only active cleaners with upcoming published assignments in current published schedules. Owner GET /v1/email/assignments/{cleaner_id}?service_date previews recipient,subject,body and fingerprint. POST to that path accepts service_date/fingerprint with Idempotency-Key and requires gmail_assignment_send_enabled. Preview includes actual house addresses/units and Eastern windows/durations/deadlines, never guest data/access codes/payment information. Revalidation blocks stale source,changed contacts/versions,exceptions,conflicting assignments,missing destinations,infeasible timing and already-started work. The durable reservation precedes SMTP; a second validation locks publication and relevant rows through sending and recording the outcome. No automatic publication or dispatch is enabled.

Local manual scheduling (2026-11-11): LOCAL_SCHEDULING_ENABLED requires the isolated read-only Hospitable workspace and local OSRM routing. Existing POST /v1/schedules/previews and POST /v1/schedules/runs/{id}/publish are enabled only for manual local action; existing auth,idempotency,If-Match,fingerprint and validation requirements remain. GET /v1/local-scheduling/policy returns current effective approval or proposed settings and a fingerprint. Owner POST to that same route approves only the reviewed fingerprint, atomically creates/approves policy and recomputes local task deadlines using imported data. No source API write occurs. Scheduled dispatch/overrides/SMS remain blocked.

Automatic email mode (2026-11-12): GET /v1/email/status reflects automatic_assignments_enabled from the explicitlocalflag; manualassignment POST is disabled whileautomation is enabled to avoid separate dispatch paths. Dashboard adds email_automation with enabled,local_time,timezone,delivery_counts and redactedproblems(cleanername/code/service_date). No new public automatic-send endpoint exists. Background publication uses approved existing policy,latest successful source,current route bindings,version checks and independent validation. The semantic email outbox has durable claims and at-most-once automatic SMTP submission after ambiguous outcomes.

Cleaner portal (2026-11-12): GET /v1/cleaner-portal uses only a scoped jobs-purpose Bearer grant,never manager cookies,returning own jobs and eligible unclaimed houses. POST accepts action accept/complete/punt/claim,assignment_id or offer_id,and expected_version with Idempotency-Key. Role identity comes from active scopedgrant; global/IP/cleaner rate limits and transaction/row locks apply. Responses refresh authoritative state. Completion beforeplannedstart,release of started/locked/completed work,staleacceptance and infeasibleclaims failclosed. Per-house acceptance is separate from whole-version acknowledgment; the latter is written only when all active jobs in that version were explicitly accepted,preventing duplicate unique-version records. Owner POST /v1/email/portal/{cleaner_id} can deliver a private welcome link using existing durable idempotent Gmail submission. Public proxy ignores manager cookies for exactcleaner/onboarding APIs and checks canonicalorigin for writes.


Workspace onboarding email (2026-11-12): POST /v1/workspace/complete retains manager authorization and Idempotency-Key, requires a successful connection, active cleaner emails and an enabled private sender, and transactionally queues initial onboarding deliveries with setup completion. It returns completed plus onboarding_emails status counts. GET /v1/workspace/onboarding-emails returns counts(total,queued,sending,accepted,uncertain,failed,stale) and enabled for the authenticated workspace. Initial invitation keys are stable per cleaner; accepted/uncertain deliveries never automatically resubmit. Onboarding-purpose grants are scoped to their cleaner/workspace and revalidated with recipient fingerprints before SMTP. GMAIL_ONBOARDING_SEND_ENABLED controls this dispatch independently of assignment/SMS settings. Provider-accepted status is not an inbox delivery guarantee.
