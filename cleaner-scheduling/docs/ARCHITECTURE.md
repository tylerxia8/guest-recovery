# Architecture

This is a proposed design, not deployed software. [PRD](../PRD.md) controls product behavior.

## Components and ownership

| Component | Responsibilities | Must not do |
| --- | --- | --- |
| Next.js + TypeScript | Authenticated manager dashboard, forms, timelines, conflict recovery | Hold provider credentials or bypass API authorization |
| FastAPI | Validate requests/JWTs, RBAC, domain services, transactions, OpenAPI | Invoke slow solver/provider calls inside DB transactions |
| PostgreSQL | Authoritative business state, version checks, durable inbox/outbox, audits | Depend on Redis for durable business facts |
| Celery + Redis | Execute idempotent jobs, wakeups, retries, solver workers | Treat broker delivery as exactly once |
| OR-Tools worker | Optimize normalized snapshot, report feasible routes/uncovered tasks | Publish a schedule or call SMS directly |
| Reservation adapter | Normalize verified source data and health | Leak source schema into task/scheduling services |
| Routing adapter | Build time-dependent travel matrix and provenance | Interpret missing legs as zero travel |
| SMS adapter | Send authorized intent, validate callbacks, normalize delivery | Equate delivery with acceptance |
| Managed auth | Authenticate operator identities | Decide domain authorization without backend enforcement |

Proposed modules: `backend/app/{api,domain,services,adapters,workers,models}`, `backend/migrations`, `backend/tests`, `frontend/{app,components,lib}`, `frontend/tests`, `infra`. These paths are implementation targets, not files delivered in this handoff. Domain services depend on interfaces; adapters depend on domain DTOs. Mock and production implementations share contract tests. Pin supported toolchain/package versions in M1.

## Explicit adapter contracts (application-owned)

`ReservationProvider.capabilities() -> {incremental,full_snapshot,webhooks,cancellations,actual_times,instructions}`; `pull(cursor?, since?) -> ReservationPage(records, next_cursor, snapshot_id?, complete, source_as_of)`; `normalize_event(raw) -> NormalizedChange[]`; `verify_webhook(raw_bytes, headers, public_url) -> VerifiedEnvelope`. Verification behavior is provider-specific and must be established before enabling ingress. Canonical reservation fields: provider/external ID, property external ID, active/canceled status, UTC check-in/out, source revision/update time if supplied, timestamp provenance. Omit guest data. Unsupported methods fail with typed `CapabilityUnavailable`.

`SmsGateway.send(intent_id, to, body, callback_url) -> SendResult(provider_message_id?, accepted, ambiguous)`; `verify_inbound(raw_bytes, headers, public_url) -> VerifiedSmsEvent`; `normalize_status(...) -> DeliveryUpdate`. Define `ProviderUnavailable`, `RateLimited(retry_after)`, `PermanentFailure`, `AmbiguousOutcome`; never blindly retry ambiguous sends. Recording mock never contacts Twilio.

`RoutingService.matrix(points, departure_bucket, mode) -> Matrix(seconds_or_unreachable, observed_at, expires_at, provider, provenance)`; `geocode(authorized_location) -> Coordinates(confidence, provenance)`. Request limits, traffic capabilities, quotas and permitted caching require verification against current Google documentation. Mock routing provides deterministic asymmetric durations including unreachable edges.

`AuthVerifier.verify(token) -> Principal(subject, roles)`; `Clock.now() -> UTC instant`; `PolicyRepository.active(version) -> ApprovedPolicy`. Inject these interfaces for deterministic time and role tests. Production auth must be unavailable in mock mode outside local/test environments.

## Durable flows

1. Provider ingress: validate signature/limits → transactionally store deduplicated inbox event → acknowledge receipt → worker normalizes and upserts reservations/tasks in a transaction → record audit, revision increment, replan outbox. Unknown/invalid events quarantine with an exception. Poll/reconciliation follows the same upsert service.
2. Preview: capture reservations, tasks, availability, rates, locks, policy and routing revisions → fetch matrix outside transaction → solve → independently validate → persist candidate and freshness fingerprint. No SMS.
3. Publication: lock service-date schedule row and relevant task/cleaner rows, compare fingerprint and expected revision, validate global schedule including adjacent dates, supersede changed assignments, copy compensation/instruction snapshots, create per-cleaner versions and SMS outbox, commit. Competing publication returns conflict. Broker wakes after commit; outbox sweeper recovers lost wakes.
4. Completion: resolve SMS sender/task/version, lock active assignment, transition once, audit and enqueue payment eligibility. Duplicate DONE returns the same result. Conflicting/canceled work routes to manager review.
5. Payments: transactionally select eligible completions, enforce globally unique earned-work key, create lines and totals; approval locks batch, validates revision and freezes lines; export reads frozen snapshot.

## Jobs and timing

Persist business-local schedule date and UTC next execution. A single leased scheduler creates unique job intents `(job_type, business_date, entity, version)`; all workers remain safe under duplicate execution. At 17:00 availability request; 17:30 proposed cutoff; by 18:00 generate/validate/publish next-day schedule; 18:00 dispatch; acknowledgment timers start on actual per-cleaner send acceptance. If preparation is late or invalid, raise an exception and dispatch only eligible approved plans. Replan events after dispatch create changed-cleaner revisions and delta SMS. Never replay yesterday's dispatch after an outage; recompute validity and ask manager for missed-window actions.

Reconciliation proposed every 15 minutes with a daily authoritative full check where supported. Retry transient jobs with bounded exponential backoff/jitter (proposed 1, 2, 4, 8, 16 minutes; honor provider retry hints), then dead-letter/exception. Retry is not a substitute for inbox/outbox idempotency. SMS has stricter ambiguous-send handling in [SMS workflows](SMS_WORKFLOWS.md).

## Infrastructure and operations

Managed cloud: separate frontend/API, worker pool with solver CPU/time limits, managed PostgreSQL with encrypted backups, private Redis, TLS load balancer and cloud secrets manager. Separate staging/production DBs, broker namespaces, provider accounts/keys and ingress URLs. Run migrations as a single controlled release job; readiness checks require DB connectivity and matching schema. Liveness checks avoid provider dependencies. Deployment rollback must not reverse destructive schema changes.

Correlate request → event → task → schedule → assignment → SMS → payment through IDs. Metrics: ingestion age/failures, inbox/outbox lag, solver feasibility/runtime, uncovered count, acknowledgment age, delivery failure, deadline misses and batch discrepancies. Alert manager via dashboard plus configured independent operator channel when SMS is impaired; do not rely solely on failed SMS. Proposed alert threshold: sync age >30 minutes or any deadline-critical uncovered job immediately. Log sensitive IDs minimally; never log access instructions, full phone numbers or raw credentials. See [security](SECURITY.md) for retention and recovery gates.
