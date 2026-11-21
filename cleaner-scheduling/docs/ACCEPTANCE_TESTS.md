# Acceptance scenarios

**Current scope (2026-11-09):** automate cleaner scheduling only. Payments, compensation setup and the standalone Settings screen are deferred. Their historical contracts and database records remain for possible restoration; they are not active features. Scheduling policies, hours, consent, eligibility, travel and deadlines remain because they determine feasible schedules.

Historical M7 finance acceptance is archived in `docs/archive/payment-tests.py.txt` and Git history; it is not executed in the scheduling-only suite. AT-20–22 and AT-36 are deferred. Current acceptance requires rate-free preview/publication/completion and absent payment/rate APIs and payment/settings pages. Production M8 gates remain open.

These are specifications for future automated tests, not tests already executed. Backend/domain scenarios use Pytest with real PostgreSQL for transaction/constraint tests; UI scenarios use Playwright. Use frozen UTC clocks, America/New_York conversions, deterministic routing, recording SMS, synthetic identities and isolated DB transactions. No live property/cleaner data or SMS. Unless overridden: checkout 10:00, arrival 16:00, approved buffer 30 minutes, duration 120 minutes, eligible cleaners with configured 09:00–17:00 shifts, sufficient capacity, consent and dated origins. Assert persisted state and outbox/audit records, not just HTTP status. IDs below are unique acceptance identifiers.

## AT-01 — Reservation import creates one departure task

- Preconditions: mapped synthetic P-A, empty reservation/task tables, mock source enabled.
- Input data: active R-A, check-in 2026-11-09T16:00:00-05:00, checkout 2026-11-10T10:00:00-05:00; next arrival R-B at 2026-11-10T16:00:00-05:00, checkout 2026-11-12T10:00:00-05:00.
- Action: import complete fixture and run task derivation.
- Expected result: two reservations, one task for R-A due 15:30 local and a task for R-B departure due within 48 elapsed hours; successful sync counters/audit.
- Pass/fail: exactly one departure task per source reservation, UTC deadline 20:30Z, provenance correct; any duplicate or invented R-B deadline fails.
- M2 subset: both reservations are persisted with the supplied offset timestamps, sync counters, and audit. Departure tasks and deadlines are M3 and are not claimed.

## AT-02 — Duplicate and reordered booking events

- Preconditions: R-A revision 2 is persisted with checkout November 11 at 10:00 local.
- Input data: same revision-2 event delivered twice, then older revision 1 with checkout November 10.
- Action: process all events and retry worker after a simulated post-commit crash.
- Expected result: revision 2 retained, one task updated once, deduplicated inbox/outbox effects; old event recorded ignored.
- Pass/fail: no regression, duplicate task or repeated schedule-change intent; audit explains ignored old update.
- M2 subset: revision 2 is retained, the duplicate does not create a second reservation, and the older revision writes `reservation.stale_revision_ignored`. Task and schedule intents are later milestones.

## AT-03 — Same-day feasible window and readiness buffer

- Preconditions: November 10 turnover, C-A available with 30-minute origin travel.
- Input data: release 10:00, arrival 16:00, buffer 30, duration 120.
- Action: generate and independently validate preview; publish it.
- Expected result: cleaning starts ≥10:00, ends ≤15:30; travel fits shift; deadline stored UTC.
- Pass/fail: all time/travel checks pass and arrival time itself is never used as cleaning finish limit when buffer applies.
- M3 subset: with an explicitly approved scenario policy, the task deadline is next check-in minus the buffer and is stored in UTC. Preview validation, travel, and publication are M4.
- M4 subset: a scenario-approved policy, 30-minute mock origin travel, and one cleaner produce a 10:00–12:00 plan that finishes by 15:30 local and publishes only as synthetic database rows. No SMS is sent.

## AT-04 — Reservation extension invalidates preview

- Preconditions: R-A departs November 10; unpublished preview includes its task.
- Input data: newer source revision extends checkout to November 11 10:00; revised adjacent stays are valid.
- Action: sync update, attempt old preview publication, then regenerate.
- Expected result: original task identity retained with new release/deadline, old preview rejected 409, no obsolete SMS.
- Pass/fail: one task, correct new window, fingerprint changes, no old schedule publication.
- M2 subset: the same reservation row keeps the extended checkout. Preview rejection and SMS are M4/M5.
- M3 subset: the same cleaning task keeps its identity and receives the extended release and deadline. Preview 409 is M4.
- M4 subset: publishing the preview after the checkout extension returns 409 and writes no assignment.

## AT-05 — Cancel source booking without erasing history

- Preconditions: unstarted task assigned for R-A; separate completed historical task exists.
- Input data: explicit source cancellation for R-A.
- Action: ingest cancellation and process revision notifications.
- Expected result: R-A's unstarted task/assignment canceled with versioned removal notice; historical completed task and compensation unchanged.
- Pass/fail: no obsolete actionable assignment, one cancellation intent, completed/payment history intact.
- M2 subset: explicit cancellation changes reservation status to canceled and keeps the row. Assignment, SMS, and payment history are later milestones.
- M3 subset: the unstarted task becomes canceled and a completed task on another reservation keeps its completion actor. Assignment cancellation is M4.
- M4 subset: source cancellation changes the published unstarted assignment to canceled. SMS removal notices are M5.

## AT-06 — Last-minute reservation creates urgency

- Preconditions: pending task without next arrival, clock November 10 12:00 local, active routes already exist.
- Input data: new arrival today at 16:00; duration 120; one eligible cleaner can arrive 12:30.
- Action: ingest arrival and targeted replan.
- Expected result: task becomes deadline-bound 15:30; feasible 12:30–14:30 assignment/new cleaner version, or explicit uncovered exception if fixture capacity removed.
- Pass/fail: update processed within proposed healthy-service 60-second target; no overlap or silence when impossible.
- M3 subset: a pending task becomes deadline-bound at 15:30 local when the next arrival appears. Assignment and replan are M4.
- M4 subset: with a shift starting 12:00 and 30-minute mock travel, the replanned preview is 12:30–14:30. The 60-second service target is not a separate timer test.

## AT-07 — Explicit cleaner unavailability

- Preconditions: C-A has two unstarted unlocked next-day jobs; C-B can feasibly cover both.
- Input data: signed inbound `UNAVAILABLE 2026-11-10` from C-A.
- Action: process command and replan.
- Expected result: C-A dated availability unavailable, jobs moved to C-B, new versions/removal notices and fresh acknowledgment required.
- Pass/fail: zero future active assignments to C-A for that day, no duplicate tasks, correct notifications and audit.
- M4 subset: manager availability, not an SMS parser, marks the date unavailable and moves unlocked future work. Removal notices and fresh acknowledgment are M5.

## AT-08 — Variable daily origins affect routing

- Preconditions: C-A/C-B identical eligibility/capacity, two distant tasks, all objectives otherwise equal.
- Input data: day one origins near opposite tasks; day two origins swapped; deterministic asymmetric matrix.
- Action: preview both dates with dated inputs.
- Expected result: route allocations follow day-specific feasible/travel-minimal origins; day-one origin never silently reused.
- Pass/fail: expected objective/travel totals and assignments match fixtures; missing day-two origin without approved fallback excludes cleaner with exception.
- M4 subset: day-specific synthetic origins change which cleaner receives each property. An available shift without an origin is rejected. A missing day-two origin does not reuse day one.

## AT-09 — Individual capacity and breaks

- Preconditions: three 120-minute tasks; C-A max one job/150 service minutes, C-B max two jobs/240 service minutes; each shift includes 12:00–12:30 break.
- Input data: feasible travel matrix and readiness deadlines.
- Action: solve and validate, then reduce C-B duty limit below route duty and solve again.
- Expected result: no caps/breaks exceeded; reduced limit either moves work feasibly or leaves explicit uncovered job.
- Pass/fail: independently recomputed job/service/duty counts, including travel and breaks, remain within individual limits.
- M4 subset: three 120-minute jobs with a 12:00–12:30 break fit the stated caps, and lowering duty leaves an explicit uncovered job.

## AT-10 — Travel conflict cannot become an impossible assignment

- Preconditions: one cleaner, two 120-minute tasks with 10:00–15:30 windows.
- Input data: 180-minute travel between jobs; no other cleaner.
- Action: generate/publish feasible subset.
- Expected result: at most one task assigned; other has deduplicated `NO_CAPACITY`/`NO_ROUTE` exception with reason.
- Pass/fail: no overlap/deadline violation; uncovered count is visible and not reported as full coverage.
- M4 subset: 180-minute travel between two jobs publishes one assignment and one deduplicated uncovered exception.

## AT-11 — Preview isolation and idempotent publication

- Preconditions: approved policy, fresh source, deterministic feasible two-cleaner run, flags allow recording mock sends.
- Input data: same preview publication key, fingerprint, day revision and If-Match sent twice.
- Action: preview, inspect SMS count, publish twice and drain outbox twice.
- Expected result: preview sends zero messages; one active published run, one intent per cleaner/version/part, identical repeat response.
- Pass/fail: exactly one logical dispatch per cleaner, no extra assignment versions on retry; stale different key cannot republish old state.
- M4 subset: preview and publish call no SMS gateway. Repeating the publish key returns the same body and does not add a second live assignment. A different key with the old fingerprint returns 409. Outbox topics are schedule records, not SMS.

## AT-12 — Delivery is not acceptance; CONFIRM is version-bound

- Preconditions: C-A has current November 10 V2; V1 superseded.
- Input data: delivered callback; `CONFIRM 2026-11-10 V1`; then `CONFIRM 2026-11-10 V2` twice.
- Action: process callbacks and inbound messages.
- Expected result: delivery alone leaves unacknowledged, V1 rejected with current-version hint, V2 creates exactly one acknowledgment.
- Pass/fail: only V2 assignments acknowledged and duplicate command no-ops.
- M5 subset: a delivered mock callback leaves the assignment unacknowledged. CONFIRM of the superseded version does not acknowledge it. CONFIRM of the current version acknowledges once, and the duplicate command does not add a second acknowledgment.

## AT-13 — Missing acknowledgment reminders and escalation

- Preconditions: proposed +30/+60-minute policy approved; schedule sent 18:00 Eastern; no reply.
- Input data: frozen clock advances to 18:30 and 19:00; duplicate timer jobs.
- Action: execute timers then inspect assignments and exceptions.
- Expected result: one reminder and one manager escalation, assignments unchanged, no automatic reassignment.
- Pass/fail: exact counts, correct current version and unchanged cleaner; late actual send shifts timers relative to actual send acceptance.
- M5 subset: timers use the message acceptance time and the approved scenario policy's 30/60 minutes. One reminder and one missing-ack escalation are created. The assignment cleaner does not change. A duplicate timer does not add another reminder. The timer is not on Celery beat.

## AT-14 — Completion ownership and deduplication

- Preconditions: T-A assigned to C-A current version; C-B registered but not assigned.
- Input data: signed `DONE T-A` from C-B, then same command twice from C-A.
- Action: process all three commands.
- Expected result: wrong sender rejected without state change; C-A completion records once with receipt time, one completed work item.
- Pass/fail: one completion transition/audit fact, no duplicate liability or forged cleaner attribution.
- M5 subset: DONE from the unassigned cleaner does not change the task. DONE from the assignee completes it once. A second DONE does not add another completed assignment. Payment batches are M7 and are not created.

## AT-15 — Automatic reassignment preserves acceptance semantics

- Preconditions: acknowledged V1 for C-A, unstarted unlocked T-A, C-B available with route room.
- Input data: manager marks C-A unavailable with expected current availability version.
- Action: apply change and automatic targeted replan.
- Expected result: old assignment superseded, C-B assignment feasible with new assignment version, C-B not auto-acknowledged; C-A removal notice.
- Pass/fail: one live assignment per task, old acknowledgment retained historically and never transferred to C-B.
- M4 subset: a manager unavailability change supersedes the acknowledged row without copying acknowledgment onto the replacement. Removal notices are M5.
- M5 subset: after a recorded mock schedule, SMS UNAVAILABLE queues a cancellation notice and does not mark the replacement acknowledged.

## AT-16 — Possibly started work and stale DONE

- Preconditions: T-A planned start already passed; C-A reports unavailable; another unstarted task can move. Manager subsequently resolves T-A and reassigns explicitly.
- Input data: UNAVAILABLE then delayed DONE T-A from old cleaner after reassignment.
- Action: process unavailability, manager resolution and delayed message.
- Expected result: initial T-A blocked for manager review rather than auto-moved; delayed completion quarantined, new assignee not marked complete.
- Pass/fail: no silent in-progress reassignment and no historical sender changing current completion/payment; evidence retained.
- M4 subset: an assignment whose planned start is already past becomes blocked when that cleaner is marked unavailable. Delayed DONE parsing is M5.
- M5 subset: DONE from the cleaner after SMS UNAVAILABLE is quarantined as a stale completion and does not complete the task.

## AT-17 — Manual overrides and locked conflicts

- Preconditions: manager locked T-A to C-A 10:00–12:00; C-A later unavailable; other unlocked tasks exist.
- Input data: solver replan and override moving another job into conflicting interval.
- Action: attempt replan/publication and conflicting override; manager then explicitly unlocks with reason.
- Expected result: lock remains intact until explicit unlock; conflict rejected/exception raised; unlocked subsequent valid plan may proceed.
- Pass/fail: audit includes actor/reasons, no infeasible publication or silent lock overwrite.
- M4 subset: a lock rejects an overlapping override and remains until an explicit unlock. Audit records the lock and unlock actors.

## AT-18 — Hospitable outage and recovery

- Preconditions: last good sync 16:00 local; proposed stale threshold 30 minutes.
- Input data: provider unavailable until 18:15, partial failed page, then complete snapshot containing a changed arrival.
- Action: attempt 18:00 dispatch, restore provider and reconcile.
- Expected result: stale-source publication blocked/escalated; cursor not advanced on partial failure; recovered changes invalidate old preview and trigger fresh planning.
- Pass/fail: no stale dispatch, no deletion from incomplete snapshot, eventual converged task state, sync health truthful.
- M2 subset: a failed page does not advance the cursor, an incomplete snapshot does not delete an omitted booking, and stale sync reports `publication_blocked`. Dispatch and task convergence are later milestones.

## AT-19 — SMS failures and ambiguous sends

- Preconditions: eligible consented cleaner, one queued assignment intent.
- Input data: permanent undeliverable callback for one fixture; second fixture times out after provider may have accepted.
- Action: process send/status and retry jobs.
- Expected result: permanent failure raises delivery exception; ambiguous send becomes unknown/reconciliation-needed, not blindly resent. Neither implies acceptance.
- Pass/fail: no unbounded retry/duplicate logical send; manager sees delivery issue independently of acknowledgment timer.
- M5 subset: an undeliverable mock outcome becomes `failed` with one delivery exception. A second drain does not send it again. Acceptance is not implied.

## AT-20 — Compensation precedence and historical stability

- Preconditions: C-A default 7,500 cents and P-B override 9,000, both effective on service date.
- Input data: completed jobs at P-A/P-B; later default becomes 8,000.
- Action: prepare batch after rate change.
- Expected result: snapshots yield 16,500 cents, P-B uses override; new default applies only to newly versioned future assignments.
- Pass/fail: exact integer total, rate versions/actor audit retained; no retroactive repricing.

## AT-21 — Duplicate/concurrent payment prevention

- Preconditions: one eligible completed task T-A with one earned-work key.
- Input data: two concurrent prepare calls with overlapping periods and different request keys; duplicate DONE and assignment-history rows also present.
- Action: execute concurrently on PostgreSQL, then repeat preparation.
- Expected result: exactly one cleaning liability globally; competing transaction skips/conflicts safely with explicit result.
- Pass/fail: count of payable cleaning lines for T-A equals one and totals are never doubled across batches.

## AT-22 — Approval and repeatable CSV export

- Preconditions: draft total 16,500 cents, expected batch version known.
- Input data: approval expected total 16,500; second approval retry; two exports; cleaner text `=SYNTHETIC()`.
- Action: approve and export, attempt to mutate approved line.
- Expected result: approval once, same financial content across exports, formula-leading text neutralized, mutation rejected, exports audited; no transfer request.
- Pass/fail: CSV integer sum matches frozen total, approved snapshot unchanged, network recorder has zero bank-transfer calls.

## AT-23 — Unauthorized access and role enforcement

- Preconditions: viewer, manager, owner, expired JWT and deactivated operator test identities.
- Input data: viewer publication/payment approval; manager rate creation; expired/deactivated dashboard access; valid authorized operations as controls.
- Action: invoke API directly and corresponding UI routes.
- Expected result: 401 invalid token, 403 insufficient/deactivated role, no side effects; authorized controls succeed. Viewer money/personal data masked or excluded.
- Pass/fail: DB/outbox unchanged on denied writes, UI cannot bypass backend, denial audit contains no secrets.
- M1 subset: API denial only. Business tables, idempotency records, and `outbox_events` stay unchanged. One sanitized `security.access_denied` audit is required and must not contain secrets. UI route coverage, payment approval, and publication routes are not claimed.

## AT-24 — DST and Eastern dispatch dates

- Preconditions: operational zone America/New_York; idempotent daily scheduler.
- Input data: dispatch November 1, 2026 at 18:00 local (23:00Z) and March 8, 2026 at 18:00 local (22:00Z); repeated fall-back scheduler clock hour.
- Action: advance clock across transitions and run duplicate scheduler ticks.
- Expected result: one dispatch intent per next-day business date at correct local 18:00; no hardcoded EST offset; reservation ambiguous date-only times require policy/provenance.
- Pass/fail: expected UTC instants and local dates exact; no double dispatch or missed day.
- M4 subset: an injected clock at 2026-11-01 23:00Z and 2026-03-08 22:00Z creates one dispatch intent per next local date. A repeated tick does not create another. Celery beat is empty. No approved policy means no intent. The intent does not publish or send.

## AT-25 — Concurrent schedule changes

- Preconditions: two managers share preview/day revision; availability changes during one publication attempt.
- Input data: concurrent publish requests with distinct keys, and stale override If-Match.
- Action: synchronize transactions to race on schedule row.
- Expected result: at most one compatible publication; stale input/version returns 409; no partial assignments or outbox.
- Pass/fail: one current run/cleaner version, lock constraints intact, losing transaction leaves no messages; UI can reload and regenerate.
- M4 subset: two concurrent publish transactions leave one published assignment. The loser returns 409. UI reload is M6.

## AT-26 — Forged/replayed webhooks

- Preconditions: verified test signature adapter, production Hospitable ingress disabled until verification.
- Input data: invalid signature, oversized body, valid event twice, reordered delivery statuses.
- Action: POST ingress fixtures and process inbox.
- Expected result: invalid/oversized rejected before business mutation; valid event persisted once; delivery state cannot regress; unverified live Hospitable ingress returns disabled.
- Pass/fail: inbox dedup keys unique, no forged completion/reservation, callback processing auditable and raw secrets absent.
- M2 subset: invalid or missing mock signatures and oversized bodies do not mutate reservations; a valid mock-signed event is stored once. Live Hospitable ingress stays disabled. SMS delivery regression is M5.
- M5 subset: mock SMS ingress rejects a missing signature and an oversized body. A repeated message id is stored once. A later `sent` callback does not downgrade `delivered`.

## AT-27 — No next arrival and predecessor cancellation

- Preconditions: R-A departure has no next booking.
- Input data: later R-B arrival added, then R-B canceled while R-A remains valid.
- Action: derive tasks after each event; separately set explicit manager target while no arrival known.
- Expected result: R-A starts due 48 hours after checkout, gets an earlier R-B-derived deadline, and returns to the 48-hour ceiling after cancellation; an explicit target is separately stored and may only tighten the deadline.
- Pass/fail: no fabricated deadline, no cancellation of R-A's valid departure task, no obsolete dispatched job after target/source change.
- M3 revised: 48-hour no-arrival deadline, earlier arrival deadline, 48-hour ceiling after cancellation, and a separately stored earlier manager target.
- M4 subset: an explicit earlier manager target tightens the scheduling window without extending the stored hard deadline.

## AT-28 — Duration hierarchy and bedroom formula

- Preconditions: approved bedroom defaults (60 + 30 × bedrooms), property P-A override 150; P-B has 5 bedrooms without override.
- Input data: turnover tasks at each property, then remove P-A override.
- Action: generate tasks/preview, then revise duration and regenerate.
- Expected result: P-A 150 initially then 120 (2-bedroom default) with new revision; P-B gets 210 minutes (60 + 30 × 5) via bedroom formula with `duration_source=bedroom_default`.
- Pass/fail: no zero-minute task; bedroom formula applies to all bedroom counts including 5+; stale preview rejected after duration change.
- M3 subset: property override 150, then bedroom formula. A 5-bedroom property uses 210-minute bedroom default. Preview rejection is M4.
- M4 subset: a 5-bedroom property with no override uses the bedroom formula (210 minutes). Adding a duration override makes the old preview return 409.

## AT-29 — Hybrid instruction import

- Preconditions: imported field `parking=synthetic north lot`; local override `parking=synthetic east lot`.
- Input data: newer source `parking=synthetic west lot`, then deliberate manager remove-override action.
- Action: import, inspect effective values, remove override with reason.
- Expected result: local east lot survives re-import; source west lot retained separately; removal switches to west lot with new version and audit, invalidating affected schedule content.
- Pass/fail: provenance/history complete; no local overwrite and no access text in general logs/SMS.
- M2 subset: a local parking override survives re-import and removal restores the newer source value. Access-code text is absent from the audit. Schedule invalidation is M4.

## AT-30 — Feature flags and launch gates

- Preconditions: development environment, all adapter modes mock, real-looking credentials absent.
- Input data: attempt dispatch with flags false; then integrations true/dispatch false; then unapproved policy with both true; production startup with mock auth.
- Action: execute scheduled and manual sends and startup validation.
- Expected result: mocks remain isolated, no external sends; dispatch denied until every required gate and approved configuration; production rejects mock auth.
- Pass/fail: network capture zero production calls in all blocked cases, clear errors and no false sent statuses.
- M1 subset: startup and dispatch-gate checks with mock providers. Flags off, integrations on with dispatch off, and both flags on with an unapproved policy all refuse a send. Production startup rejects mock auth. No Twilio sandbox call is made. Full credentialed launch remains open.
- M5 subset: `dispatch_tick` still reports no send. Turning the production dispatch flag on blocks mock recording and does not call the gateway. No Twilio client is constructed.

## AT-31 — STOP, duplicate commands and ambiguous CONFIRM

- Preconditions: C-A has actionable versions for two different service dates and a queued reminder.
- Input data: bare CONFIRM, STOP twice, subsequent assignment retry and CONFIRM.
- Action: process in order with a concurrent worker send blocked until STOP commits.
- Expected result: ambiguous CONFIRM changes nothing; STOP persists once, suppresses queued sends, keeps physical work visible with contact exception; later CONFIRM does not clear opt-out.
- Pass/fail: zero operational sends after opt-out commit; no default date guessing or consent reset.
- M5 subset: bare CONFIRM with more than one current date changes nothing. STOP opts out once, cancels queued mock sends, and is not cleared by a later CONFIRM. The cleaning assignment row remains.

## AT-32 — Required dashboard and exception lifecycle

- Preconditions: synthetic today/tomorrow schedules, one missing ack, one late job, one stale sync, failed SMS, unassigned carryover task.
- Input data: manager opens dashboard, filters exceptions, assigns owner and tries to resolve a still-infeasible locked conflict.
- Action: run Playwright flow and direct API assertions.
- Expected result: all PRD dashboard panels visible, statuses agree with DB, pending/uncovered work prominent; ownership update succeeds, false resolution rejected.
- Pass/fail: schedule/availability/assignment/ack/completion/overrides/sync/SMS status all present; viewer lacks monetary details and writes.
- M4 subset: uncovered work is stored on the schedule run and as an open exception. The dashboard UI, acknowledgment, SMS, and payment panels are M6 and are not claimed.
- M6 subset: the dashboard renders today, tomorrow, availability, assignments, acknowledgments, completion, sync, SMS delivery, uncovered/pending work. Payment and settings panels are absent. Viewers have no resolve control. A locked-conflict exception can be assigned but cannot be resolved. Stale `If-Match` returns 409. A populated draft payment batch is M7.

## AT-33 — Message versioning and long-schedule chunks

- Preconditions: long schedule requires three parts; V1 gets revised to V2 before all parts send.
- Input data: duplicate outbox deliveries, old part retries, stale V1 CONFIRM, malformed `DONE` without ID and `DONE T-A EXTRA`.
- Action: drain sends, parse inbound messages.
- Expected result: one intent per version/part; old unsent parts canceled; V2 parts labeled coherently; stale/malformed commands rejected with safe guidance.
- Pass/fail: no duplicate part intent, no stale acknowledgment/completion, unchanged cleaners preserve their prior acknowledgment.
- M5 subset: three jobs produce three mock assignment parts. `DONE` without an id and `DONE` with an extra token do not complete a task.

## AT-34 — Sensitive access and recovery controls

- Preconditions: encrypted synthetic entry instruction, scoped cleaner link for C-A, expired and C-B link tokens; isolated backup with pending outbox.
- Input data: unauthorized link reads, log capture, restored DB with dispatch default off.
- Action: attempt cross-cleaner/expired access, inspect logs/CSV, restore and reconcile provider send history before resumption.
- Expected result: unauthorized access denied; no codes/phones/tokens in logs/CSV; backup restores relational/compensation integrity without replaying accepted SMS.
- Pass/fail: secrets absent, role/link checks hold, timed restore meets approved RPO/RTO or launch remains blocked with measured shortfall.
- M1 subset: log redaction of bearer tokens, phone numbers, access-code text, and API-key-shaped strings, plus absence of provider credentials in the frontend shell. Cleaner links, CSV export, and the backup restore drill are not claimed.
- M5 subset: a hashed cleaner link returns that cleaner's public assignment fields, an expired link is not found, and the application log does not contain the token or phone. A second mock drain does not resend an already recorded message. Backup restore and RPO/RTO are not claimed.

## AT-35 — Authorized fallback import and partial snapshots

- Preconditions: API mode unavailable, manager-approved CSV mode, mapped properties.
- Input data: CSV with offset timestamps and ordered revision; second file has conflicting duplicate IDs; simulated feed snapshot incomplete and omits existing reservation.
- Action: preview/commit valid import twice, preview invalid file, process incomplete feed.
- Expected result: valid import idempotent; conflicting file cannot commit; omitted booking retained; dashboard labels fallback latency and source mode.
- Pass/fail: no fabricated API success, deletion or real-time promise; unauthorized feed host rejected; no cursor advancement on failed pages.
- M2 subset: CSV commit is idempotent, conflicting rows cannot commit, an omitted booking is retained, and a non-allowlisted feed host is rejected without a live fetch. Dashboard labeling is M6.

## AT-36 — Rate/date boundaries, missing rates and void/reprepare

- Preconditions: rate 7,500 effective before November 11, 8,000 from November 11; two jobs service dates November 10/11 complete November 12; third cleaner lacks a rate.
- Input data: period `[2026-11-10,2026-11-12)`, later overlapping preparation, draft void then reprepare.
- Action: calculate using frozen snapshots/service dates, prepare, void draft with reason, reprepare while retaining history.
- Expected result: 15,500 cents for two eligible jobs, third job excluded with explicit missing-rate exception; void/reprepare reuses/moves unique earned-work liabilities rather than copying them.
- Pass/fail: date boundaries and effective rules exact; no zero-default rate, duplicate payable line or erased history; negative adjustment requires approved reason/policy.


## M8 bounded route optimization regression coverage

`backend/tests/test_route_optimization.py` covers AT-09/AT-10/AT-17 subsets:
coverage lost by greedy ordering, exact lock preservation with work before a
lock, recomputed incoming travel, absent direct routes reachable via a predecessor,
travel and service around breaks, waiting across a break, cleaner eligibility,
daily caps and independent rejection of an omitted lock. Twelve seeded small
instances are checked against exhaustive route permutations for coverage and
travel. A two-lock regression ensures an independently valid plan replaces the
fallback when its claimed incoming travel is wrong. The reproducible benchmark
is `backend/scripts/benchmark_routes.py`; see [evidence](OPEN_SOURCE_EVALUATION.md).
These tests do not establish all five objective stages, full override validation,
production routing, full-scale optimality, multi-day planning or launch readiness.


## FR-28 / M8 manager setup and proxy demonstration

`tests/test_setup.py` verifies configuration/readiness indicators without a live
activation switch, server-clock Eastern dates, workday timezone/DST and break
validation, denied viewer writes/read restrictions, stale versions/idempotency,
property pagination/redaction, proxy turnover ingestion and a mock-only loop that
publishes once, preserves unchanged versions/messages, surfaces newly uncovered
work and pauses after failure. Standard-app routes exclude the proxy controls.
`frontend/tests/demo.spec.ts` adds a manager journey through house/cleaner creation,
eligibility/caps, working hours, the simulated account, start/pause, dashboard
refresh feedback and viewer control removal. The dashboard browser regression
advances its clock to verify automatic data replacement and retained-data stale
feedback after an API failure. This is a bounded synthetic subset;
[remaining requirements](MANAGER_SETUP.md) still block unattended production.

### Local housing presentation

- With optional local housing context, a manager sees each linked house's public property type and dated tax assessment, a source link, retrieval date, and explicit labels for unverified bedrooms/cleaning assumptions. No rental/management status is implied.
- Without the file, the synthetic demo continues unchanged. Regular application returns 404 for `/v1/demo/housing`; demo viewers receive 403. Test fixtures contain no real addresses.
- Covered by `tests/test_demo_housing.py`, `tests/test_demo.py`, and `frontend/tests/housing.spec.ts`.

### Daily board and evening records (FR-03/04/11/18/25)

- A departure without another arrival is ready with a hard deadline 48 elapsed hours after checkout; an earlier buffered move-in wins. Check both DST transitions. A manager target can tighten but never extend the ceiling. Canceling the next arrival restores the 48-hour ceiling.
- Unassigned released work appears on later feasible days. Published assignments appear on their service date, with cleaner/time/deadline and explicit completion facts. Exclude canceled work and historical assignments from daily totals. An overdue house remains visible regardless of selected date. A live assignment on another date cannot be duplicated.
- Before 18:00 Eastern the automatic recording seam queues no evening schedule. At/after cutoff it records next-day assignments with house, times, deadline and explicit CONFIRM/DONE commands. Repeated ticks/calls do not duplicate version/part intents. The guarded 6 PM simulation is manager-only; all production dispatch remains disabled.
- Evidence: `tests/test_daily_operations.py`, revised `test_task_generation.py`/`test_scheduling.py`, evening authorization/retry checks in `test_setup.py`, and `frontend/tests/daily-cleaning.spec.ts`. Full multi-day optimization and durable production dispatch are not claimed.

### Saved driving baseline (FR-07/09/10/25)

- Capture directed road times once with provenance; successfully saved responses and final baseline are reusable offline. Preserve different A-to-B/B-to-A durations, ceil duration values conservatively, and never replace missing legs by zero/synthetic travel.
- Include approved demo staging-to-first-job and stop-to-stop times in order, travel gaps and duty constraints. Independent validation uses the same matrix. A missing leg leaves work uncovered; a changed baseline rejects an old preview. Invalid/stale house bindings cannot load.
- Display drive minutes, previous stop and original route source on daily rows; show capture/source/staging/traffic assumptions. Preserve historical/started/locked assignments rather than silently altering them.
- Automated evidence: `test_driving_baseline.py` and the driving assertions in `daily-cleaning.spec.ts`; all fixture locations are fictional and no external request runs in tests.

## Condensed Hospitable-first workflow

`test_setup_only_requests_cleaning_times_missing_from_approved_defaults` distinguishes missing policy/defaults, valid defaults, unsupported bedroom counts and explicit overrides. `frontend/tests/hospitable-setup.spec.ts` verifies four navigation destinations, combined house/reservation review, no visible duplicate-entry forms and the explicit simulated/live-import distinction. The isolated demo tour still exercises optional house creation under Demo tools, cleaner setup, manual intervention and automated scheduling.

## Schedule loading after dashboard navigation

`frontend/tests/schedule-loading.spec.ts` verifies that server-rendered schedule/date/preview controls remain disabled before JavaScript is ready and that a direct hydrated visit fetches and renders the active schedule. The demo tour also awaits and checks the schedule API response. The dashboard uses client navigation for its assignment-review link. These checks guard against a native form reload swallowing an early Load schedule click.

### One-click demo connection

The isolated demo tour connects using the single button and lands on the manager dashboard. It verifies role-selector absence, an explicit simulated-connection label, HttpOnly token storage, recoverable failure/retry, and viewer authorization via test sessions. `frontend/tests/hospitable-connect.spec.ts` checks that live connection is disabled outside demo mode and cross-origin requests cannot establish a session.


M8 weekly manager-flow acceptance: connect to the simulated Hospitable account,
configure cleaner eligibility/capacity and regular weekly hours, explicitly start
demo automation, then open Tomorrow. Weekly hours repeat across dates and DST
using Eastern civil time; dated exceptions win, days off remain unavailable,
stale edits/previews are rejected and repeated requests are idempotent. Explicit
UNAVAILABLE on a weekly-only date must preserve locks and use existing replanning.
Manager browser coverage saves weekly hours without a dated entry. Viewer coverage
loads tomorrow from the business clock, filters cleaners, exposes uncovered work
and labels stale data after refresh failure. No public unauthenticated schedule
or production SMS is enabled. Evidence: test_weekly_hours.py, weekly-workflow.spec.ts,
and the existing demo/setup/scheduling/SMS regressions.


M8 cleaner-input acceptance: formatted phone entry normalizes before duplicate
checks; cleaner creation needs no technical caps. Working hours/travel/breaks
still bound feasible work. Weekly starting address is editable text, persists
for manager reload, never appears in viewer data or audit/idempotent responses,
and saves a measured directed baseline for first-job travel. Repeated saves of
the same resolved address/baseline do not request online routes again. Missing,
ambiguous, out-of-area, failed or road-snap-rejected origins never fall back to
synthetic routes. See test_cleaner_inputs.py and weekly-workflow.spec.ts.


M8 cleaner-removal acceptance: manager can cancel or confirm Remove cleaner;
viewer has no removal controls and API writes are forbidden. Removal disappears
from active team selection and new scheduling, preserves completed history,
releases future unlocked work for feasible reassignment, protects locked and
possibly started assignments with exceptions, revokes links and cancels queued
messages. Stale edits/previews fail and repeat requests are idempotent. Coverage:
test_cleaner_removal.py, remove-cleaner.spec.ts and scheduling/SMS regressions.

M8 theme presentation check: Wada 268 palette (olive #253122, sienna CTA #a56321, mizuiro #bce4e5, umenezu #b59392, paper #f7f6f2) replaces the retired rose/scarlet theme. The header top border and brand-symbol use olive; CTA buttons use --cta #a56321 with white text; navigation selection uses --cta border; focus-visible uses --focus (olive light, mizuiro dark) at 3px. Inspect desktop/mobile dashboard and cleaner setup, including selected navigation, focus, tables and exception feedback. Normal text on brand backgrounds must retain at least 4.5:1 contrast (ink-on-paper 12.601:1, cta-text-on-cta 4.766:1). No rose hex (#e54a6f/#e76c8f) or old --primary (#c5222e) values remain in frontend/. Automated regression: test_visual_tokens.py verifies no forbidden hex appears in frontend/**/*.{css,tsx}.
