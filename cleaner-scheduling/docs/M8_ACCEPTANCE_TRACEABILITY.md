# M8 Acceptance Traceability

**Status:** M8 in progress (2026-11-11). This document maps PRD functional requirements (FR) and acceptance tests (AT) to implementation code and test files.

**Legend:**
- ✅ Fully covered by automated tests
- ⚠️ Partially covered (subset tested)
- ❌ Not covered or deferred
- 📁 File reference
- 🧪 Test reference

---

## Functional Requirements Coverage

### FR-01: Reservation Import
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Provider adapter interface | 📁 `backend/app/adapters/reservations/base.py` | |
| Mock provider | 📁 `backend/app/adapters/reservations/mock.py` | 🧪 `test_reservation_ingestion.py::test_at01_*` |
| CSV import | 📁 `backend/app/adapters/reservations/import_csv.py` | 🧪 `test_reservation_ingestion.py::test_csv_*` |
| Sync service | 📁 `backend/app/services/reservation_sync.py` | 🧪 `test_reservation_ingestion.py` |
| Source IDs/versions preserved | 📁 `backend/app/models/tables.py` (Reservation model) | 🧪 `test_at01_mock_import_persists_two_reservations_without_tasks` |

### FR-02: Deduplication and Reconciliation
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Duplicate event handling | 📁 `backend/app/services/reservation_sync.py` | 🧪 `test_at02_duplicate_and_older_revision_do_not_regress` |
| Out-of-order updates | 📁 `backend/app/workers/reservations.py` | 🧪 `test_at02_duplicate_and_older_revision_do_not_regress` |
| Incomplete import safety | 📁 `backend/app/services/reservation_sync.py` | 🧪 `test_reservation_ingestion.py::test_incomplete_snapshot_*` |

### FR-03: Task Generation
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| One task per departure | 📁 `backend/app/services/task_generation.py` | 🧪 `test_task_generation.py` |
| Release/deadline recomputation | 📁 `backend/app/domain/tasks.py` | 🧪 `test_at01_deadline_uses_supplied_buffer_not_arrival_time` |
| Completion history preservation | 📁 `backend/app/services/task_generation.py` | 🧪 `test_task_generation.py::test_source_cancel_preserves_completed_history` |

### FR-04: Same-day Turnover Deadlines
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Readiness buffer calculation | 📁 `backend/app/domain/tasks.py::effective_deadline` | 🧪 `test_at03_feasible_window_publishes_without_using_arrival_as_deadline` |
| Same-day deadline priority | 📁 `backend/app/services/schedules.py::_inputs` | 🧪 `test_at06_last_minute_arrival_is_scheduled_after_travel` |

### FR-05: Duration Estimation
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Property override | 📁 `backend/app/models/tables.py` (Property.duration_override_minutes) | 🧪 `test_at28_five_bedroom_property_uses_formula_duration_and_override_marks_stale` |
| Bedroom defaults | 📁 `backend/app/domain/policy_schema.py` | 🧪 `test_setup.py::test_setup_*` |
| Bedroom formula | 📁 `backend/app/services/task_generation.py::resolve_duration` | 🧪 `test_at28_five_bedroom_property_uses_formula_duration_and_override_marks_stale` |
| Duration hierarchy | 📁 `backend/app/services/task_generation.py` | 🧪 `test_task_generation.py::test_duration_override_then_bedroom_default_and_five_bedroom_uses_formula` |
| Invalid overrides | 📁 `backend/app/api/routes/tasks.py::DurationPatch` | 🧪 `test_task_generation.py::test_fr05_rejects_invalid_duration_overrides` |

### FR-06: Cleaner Availability
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Working hours | 📁 `backend/app/services/weekly_availability.py` | 🧪 `test_weekly_hours.py` |
| Dated exclusions | 📁 `backend/app/models/tables.py` (CleanerAvailability) | 🧪 `test_at07_unavailability_moves_unlocked_future_work` |
| Property eligibility | 📁 `backend/app/models/tables.py` (CleanerEligibility) | 🧪 `test_scheduling.py` |
| SMS consent | 📁 `backend/app/models/tables.py` (Cleaner.sms_consent_status) | 🧪 `test_sms.py` |

### FR-07: Starting Location
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Dated origins | 📁 `backend/app/services/weekly_availability.py` | 🧪 `test_at08_day_specific_origins` |
| Missing origin rejection | 📁 `backend/app/services/schedules.py::_inputs` | 🧪 `test_at08_day_specific_origins` |
| Saved driving baseline | 📁 `backend/app/services/starting_address.py` | 🧪 `test_driving_baseline.py` |

### FR-08: Individual Capacity Limits
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Max jobs/service/duty | 📁 `backend/app/domain/scheduling.py` (Shift) | 🧪 `test_at09_caps_and_breaks_are_enforced` |
| Breaks enforcement | 📁 `backend/app/domain/scheduling.py::_sequence` | 🧪 `test_route_optimization.py::test_travel_cannot_use_a_break` |
| Dated capacity overrides | 📁 `backend/app/models/tables.py` (CleanerAvailability) | 🧪 `test_at09_caps_and_breaks_are_enforced` |

### FR-09: OR-Tools Optimization
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| OR-Tools solver | 📁 `backend/app/domain/scheduling.py::solve` | 🧪 `test_route_optimization.py` |
| Travel duration constraints | 📁 `backend/app/domain/scheduling.py` | 🧪 `test_route_optimization.py::test_joint_routes_*` |
| No overlap constraint | 📁 `backend/app/domain/scheduling.py::validate` | 🧪 `test_at10_long_travel_leaves_one_job_uncovered` |

### FR-10: Schedule Previews
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Preview generation | 📁 `backend/app/services/schedules.py::preview_schedule` | 🧪 `test_at11_preview_is_silent_and_publish_is_idempotent` |
| Input fingerprinting | 📁 `backend/app/services/schedules.py::_fingerprint` | 🧪 `test_at04_extension_rejects_stale_preview` |
| No SMS from preview | 📁 `backend/app/services/schedules.py` | 🧪 `test_at11_preview_is_silent_and_publish_is_idempotent` |

### FR-11: Schedule Publication
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| 18:00 dispatch clock | 📁 `backend/app/workers/dispatch_clock.py` | 🧪 `test_at24_dispatch_clock_is_idempotent_and_not_scheduled` |
| Freshness checks | 📁 `backend/app/services/schedule_freshness.py` | 🧪 `test_at04_extension_rejects_stale_preview` |
| One active version | 📁 `backend/app/services/schedules.py::publish_schedule` | 🧪 `test_at25_concurrent_publish_keeps_one_run` |

### FR-12: SMS Gateway
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Mock SMS gateway | 📁 `backend/app/adapters/sms/mock.py` | 🧪 `test_sms.py` |
| Intent deduplication | 📁 `backend/app/services/sms_lifecycle.py` | 🧪 `test_at11_preview_is_silent_and_publish_is_idempotent` |
| STOP/consent handling | 📁 `backend/app/domain/sms_commands.py` | 🧪 `test_at31_stop_suppresses_sends_and_bare_confirm_is_ambiguous` |

### FR-13: Cleaner Acceptance
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| CONFIRM command | 📁 `backend/app/domain/sms_commands.py` | 🧪 `test_at12_delivery_is_not_acceptance_and_confirm_is_versioned` |
| Version binding | 📁 `backend/app/services/sms_lifecycle.py` | 🧪 `test_at12_delivery_is_not_acceptance_and_confirm_is_versioned` |
| Reminders/escalations | 📁 `backend/app/services/sms_lifecycle.py::run_ack_timers` | 🧪 `test_at13_reminder_and_escalation_do_not_reassign` |

### FR-14: UNAVAILABLE Processing
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| SMS UNAVAILABLE | 📁 `backend/app/domain/sms_commands.py` | 🧪 `test_at07_unavailability_moves_unlocked_future_work` |
| Manager unavailability | 📁 `backend/app/services/schedules.py::mark_unavailable` | 🧪 `test_at07_unavailability_moves_unlocked_future_work` |
| Replan trigger | 📁 `backend/app/services/schedules.py::mark_unavailable` | 🧪 `test_at15_replan_does_not_transfer_acknowledgment` |

### FR-15: Automatic Reassignment
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Unlocked work reassignment | 📁 `backend/app/services/schedules.py::mark_unavailable` | 🧪 `test_at07_unavailability_moves_unlocked_future_work` |
| Lock preservation | 📁 `backend/app/services/schedules.py::mark_unavailable` | 🧪 `test_at16_started_work_is_blocked_not_moved` |
| Acknowledgment not transferred | 📁 `backend/app/services/schedules.py` | 🧪 `test_at15_replan_does_not_transfer_acknowledgment` |

### FR-16: Last-minute Bookings
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Deadline revision | 📁 `backend/app/services/task_generation.py` | 🧪 `test_at06_last_minute_arrival_is_scheduled_after_travel` |
| Urgent replan | 📁 `backend/app/services/schedules.py::preview_schedule` | 🧪 `test_at06_last_minute_arrival_is_scheduled_after_travel` |

### FR-17: Cancellation/Extension Reconciliation
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Extension handling | 📁 `backend/app/services/task_generation.py` | 🧪 `test_at04_extension_rejects_stale_preview` |
| Cancellation handling | 📁 `backend/app/services/task_generation.py` | 🧪 `test_at05_source_cancel_cancels_unstarted_assignment` |
| History preservation | 📁 `backend/app/services/task_generation.py` | 🧪 `test_task_generation.py::test_source_cancel_preserves_completed_history` |

### FR-18: Completion Processing
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| DONE command | 📁 `backend/app/domain/sms_commands.py` | 🧪 `test_at14_done_is_owned_and_deduplicated` |
| Authorized completion | 📁 `backend/app/services/sms_lifecycle.py` | 🧪 `test_at14_done_is_owned_and_deduplicated` |
| Deduplication | 📁 `backend/app/services/sms_lifecycle.py` | 🧪 `test_at14_done_is_owned_and_deduplicated` |

### FR-19: Manager Lock/Override
**Status:** ⚠️ Partially covered

| Aspect | Implementation | Tests | Gap |
|--------|----------------|-------|-----|
| Lock API | 📁 `backend/app/api/routes/schedules.py::post_lock` | 🧪 `test_at17_lock_survives_conflict_until_unlock` | ✅ |
| Override API | 📁 `backend/app/api/routes/schedules.py::post_override` | 🧪 `test_at17_lock_survives_conflict_until_unlock` | ✅ |
| Conflict rejection | 📁 `backend/app/services/schedules.py::override_assignment` | 🧪 `test_at17_lock_survives_conflict_until_unlock` | ✅ |
| Lock UI controls | ❌ Not implemented | ❌ | **Missing: Dashboard lock/unlock/reassign UI** |
| Publication block on infeasible | 📁 `backend/app/services/schedules.py::_reject_broken_locks` | 🧪 `test_route_optimization.py::test_independent_validator_rejects_dropped_lock` | ✅ |
| Viewer lock denial | ❌ Not tested | ❌ | **Missing: Playwright test for viewer denial** |

### FR-20: Instruction Import
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Instruction versioning | 📁 `backend/app/services/instructions.py` | 🧪 `test_reservation_ingestion.py::test_instruction_override_survives_reimport` |
| Local overrides | 📁 `backend/app/services/instructions.py` | 🧪 `test_reservation_ingestion.py::test_instruction_override_survives_reimport` |
| Provenance tracking | 📁 `backend/app/models/tables.py` (PropertyInstruction) | 🧪 `test_reservation_ingestion.py` |

### FR-21: Compensation (Deferred)
**Status:** ❌ Deferred

Archived to `docs/archive/`. Historical models preserved but not active.

### FR-22: Payment (Deferred)
**Status:** ❌ Deferred

Archived to `docs/archive/`. No transfers implemented or enabled.

### FR-23: Exception Queue
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Exception creation | 📁 `backend/app/services/exceptions.py` | 🧪 `test_scheduling.py` |
| Dashboard endpoint | 📁 `backend/app/api/routes/dashboard.py` | 🧪 `test_dashboard.py` |
| Resolution workflow | 📁 `backend/app/services/exceptions.py` | 🧪 `test_dashboard.py` |
| Browser tests | | 🧪 `frontend/tests/dashboard.spec.ts` |

### FR-24: Audit Logging
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Audit service | 📁 `backend/app/services/audit.py` | 🧪 `test_authz.py` |
| Redacted changes | 📁 `backend/app/services/redaction.py` | 🧪 `test_redaction.py` |
| Actor/timestamp | 📁 `backend/app/services/audit.py::write_audit` | 🧪 `test_authz.py` |

### FR-25: Dashboard
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Daily board | 📁 `backend/app/services/dashboard.py` | 🧪 `test_dashboard.py` |
| Filters | 📁 `backend/app/api/routes/dashboard.py` | 🧪 `test_dashboard.py` |
| Browser tests | | 🧪 `frontend/tests/daily-cleaning.spec.ts` |
| Role enforcement | 📁 `backend/app/api/deps.py` | 🧪 `frontend/tests/dashboard.spec.ts` |

### FR-26: Production Gates
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Dispatch gate | 📁 `backend/app/services/dispatch_gate.py` | 🧪 `test_dispatch_gate.py` |
| Mock-only enforcement | 📁 `backend/app/services/schedules.py::assert_synthetic_publication` | 🧪 `test_at30_flags_still_refuse_live_and_block_recording_when_enabled` |
| Startup validation | 📁 `backend/app/config.py::validate_startup` | 🧪 `test_config.py` |

### FR-27: Security
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Role-based access | 📁 `backend/app/api/deps.py` | 🧪 `test_authz.py` |
| Log redaction | 📁 `backend/app/logging_redaction.py` | 🧪 `test_redaction.py` |
| Cleaner links | 📁 `backend/app/api/routes/sms.py` | 🧪 `test_at34_link_is_scoped_and_logs_omit_the_token` |

### FR-28: Hospitable Setup
**Status:** ✅ Fully covered

| Aspect | Implementation | Tests |
|--------|----------------|-------|
| Setup endpoint | 📁 `backend/app/api/routes/setup.py` | 🧪 `test_setup.py` |
| Demo automation | 📁 `backend/app/demo/automation.py` | 🧪 `test_demo.py` |
| Browser workflow | | 🧪 `frontend/tests/hospitable-setup.spec.ts` |
| Simulated connection | 📁 `backend/app/demo/server.py` | 🧪 `frontend/tests/hospitable-connect.spec.ts` |

---

## Acceptance Test Coverage

| AT ID | Description | Backend Test | Browser Test | Status |
|-------|-------------|--------------|--------------|--------|
| AT-01 | Reservation import | `test_at01_*` (2 tests) | - | ✅ |
| AT-02 | Duplicate/reordered events | `test_at02_*` | - | ✅ |
| AT-03 | Feasible window | `test_at03_*` | - | ✅ |
| AT-04 | Extension rejects stale | `test_at04_*` | - | ✅ |
| AT-05 | Source cancellation | `test_at05_*` | - | ✅ |
| AT-06 | Last-minute arrival | `test_at06_*` | - | ✅ |
| AT-07 | Cleaner unavailability | `test_at07_*` | - | ✅ |
| AT-08 | Day-specific origins | `test_at08_*` | - | ✅ |
| AT-09 | Caps and breaks | `test_at09_*` | - | ✅ |
| AT-10 | Travel conflict | `test_at10_*` | - | ✅ |
| AT-11 | Preview isolation | `test_at11_*` | - | ✅ |
| AT-12 | Delivery vs acceptance | `test_at12_*` | - | ✅ |
| AT-13 | Reminders/escalation | `test_at13_*` | - | ✅ |
| AT-14 | Completion ownership | `test_at14_*` | - | ✅ |
| AT-15 | Replan semantics | `test_at15_*` | - | ✅ |
| AT-16 | Started work blocked | `test_at16_*` | - | ✅ |
| AT-17 | Lock survives conflict | `test_at17_*` | - | ✅ |
| AT-18 | Hospitable outage | `test_reservation_ingestion.py` (subset) | - | ⚠️ |
| AT-19 | SMS failures | `test_at19_*` | - | ✅ |
| AT-20 | Compensation precedence | Archived (deferred) | - | ❌ |
| AT-21 | Payment deduplication | Archived (deferred) | - | ❌ |
| AT-22 | Approval/CSV export | Archived (deferred) | - | ❌ |
| AT-23 | Role enforcement | `test_authz.py` | `dashboard.spec.ts` | ✅ |
| AT-24 | DST dispatch dates | `test_at24_*` | - | ✅ |
| AT-25 | Concurrent publish | `test_at25_*` | - | ✅ |
| AT-26 | Forged webhooks | `test_at26_*` | - | ✅ |
| AT-27 | No next arrival (48h cap) | `test_at27_*`, `test_next_arrival_cancellation_restores_48h_deadline_cap` | - | ✅ |
| AT-28 | Duration hierarchy | `test_at28_*`, `test_duration_override_then_bedroom_default_and_five_bedroom_uses_formula` | - | ✅ |
| AT-29 | Hybrid instructions | `test_instruction_override_survives_reimport` | - | ⚠️ |
| AT-30 | Feature flags | `test_at30_*` | - | ✅ |
| AT-31 | STOP/duplicate commands | `test_at31_*` | - | ✅ |
| AT-32 | Dashboard panels | `test_dashboard.py` | `dashboard.spec.ts` | ✅ |
| AT-33 | Message versioning | `test_at33_*` | - | ✅ |
| AT-34 | Sensitive access | `test_at34_*` | - | ✅ |
| AT-35 | Fallback import | `test_reservation_ingestion.py` | - | ⚠️ |
| AT-36 | Rate boundaries | Archived (deferred) | - | ❌ |

---

## Identified Gaps for PR 2

### Manager Lock/Override UI (FR-19)

The backend API for locking, unlocking, and overriding assignments exists and is tested:
- `POST /assignments/{id}/lock` - lock/unlock with reason
- `POST /assignments/{id}/override` - reassign to different cleaner

**Missing:**
1. Dashboard UI controls for lock/unlock/reassign
2. Playwright tests for:
   - Manager lock/unlock workflow
   - Viewer denial of lock controls
   - Blocked publish when locks conflict

### Lock Conflict Exception Surfacing

The backend creates `LOCK_CONFLICT` exceptions when:
- An override overlaps a locked assignment
- Publication would drop a locked assignment

**Missing:**
1. Dashboard display of LOCK_CONFLICT exceptions with resolution flow
2. Playwright test for blocked-publish path

---

## Document Contradictions Fixed

### OQ-12 vs PRD (2026-11-07 revision)

**Before:** OQ-12 stated "no arrival means pending without deadline"

**After:** Updated to match PRD: "no arrival means checkout plus 48 elapsed hours"

The PRD 2026-11-07 revision explicitly states:
> "Without a next arrival, checkout plus 48 hours still applies."

### M3 Scope Boundary

**Before:** Referenced "pending state when no next arrival"

**After:** Updated to reflect the 48-hour deadline rule from the PRD revision.

---

## Test Execution Evidence

**Backend tests:** 151 passed (clock freeze applied in conftest.py)

**Frontend build:** Successful (`npx next build`)

**Playwright tests:** 17 passed, 3 skipped (demo tests require separate environment)

---

## Clock Freeze Status (Resolved)

The test suite uses test data with dates in November 2026. As the real system date advances past November 10, 2026, tests that create scheduling data for that date would fail with `DEADLINE_IMPOSSIBLE` because the system clock would be past the task deadlines.

**Root cause (resolved):** `backend/tests/conftest.py` originally created a `client` fixture that used real time instead of a frozen clock.

**Fix applied:** The PR updated conftest.py to use `TEST_CLOCK = FrozenClock(datetime(2026, 11, 10, 14, 0, tzinfo=UTC))` for the test client, ensuring tests run consistently regardless of the actual system date.

**Note:** The `reset_database` fixture uses `TEST_CLOCK` for operator/property seeding. Some `test_sms.py` helpers use real time for operator/property seed timestamps, which is acceptable since those timestamps are not deadline-sensitive.

---

## Production Clock Bypass Report

The following production code locations use `datetime.now(UTC)` or `SystemClock()` directly instead of the injected clock:

| File | Line | Usage | Impact |
|------|------|-------|--------|
| `services/schedules.py` | 914 | `datetime.now(UTC)` in `_fingerprint` | Schedule day creation timestamp |
| `services/starting_address.py` | 111 | `datetime.now(UTC)` for `captured_at` | Routing cache timestamp |
| `adapters/routing/mock.py` | 31 | `datetime.now(UTC)` for observed | Mock routing adapter |
| `adapters/reservations/mock.py` | 59,71,83,121 | `datetime.now(UTC)` for observed | Mock reservation adapter |
| `demo/automation.py` | 163 | `datetime.now(UTC)` for `last_run_at` | Demo automation state |
| `demo/server.py` | 56 | `datetime.now(UTC)` | Demo server |
| `seed.py` | 84-85 | `SystemClock()` | Database seeding |
| `workers/reservations.py` | 17 | `SystemClock()` | Worker execution |

These are generally acceptable for:
- Mock/demo code (not production behavior)
- Seeding and observation timestamps (not scheduling-critical)
- Worker entry points that receive clock from the app factory

The scheduling-critical paths (`services/schedules.py`, `services/sms_lifecycle.py`, etc.) use the injected clock parameter.
