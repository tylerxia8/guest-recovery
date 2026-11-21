# Scheduling engine contract

Eligibility revision (2026-11-10): new cleaners cover all current and future active houses except explicit manager exclusions. Resolve that policy in one shared eligibility function for setup readiness, exclusion editing and solver inputs. Preserve legacy allowlists until a manager deliberately converts them. Eligibility updates increment cleaner/day revisions under the publication lock so existing previews cannot publish stale eligibility.

Planned house-handoff contract, FR-29 (not yet implemented): an explicit punt releases only that unstarted, unlocked task, records who declined it and creates a durable offer generation. The punter remains available for other work. Ordinary automatic allocation must not reclaim open offers or bounce the task back to a prior decliner. A different eligible cleaner explicitly claims the offer. Serialize claims with publication and check current source/task state, all existing route commitments, working hours, breaks, capacities, travel and deadline feasibility; independently validate before assigning. First successful claim wins; repeated or competing claims cannot duplicate ownership. Keep unrelated jobs pinned and preserve their ownership/times. Refresh only affected cleaners' schedule versions and require explicit acceptance. Locked, completed or possibly started work requires manager resolution.

The manager approved a 120-minute response window, measured from the accepted punt. Persist the expiry on the offer. At expiry, create an uncovered-house exception and a deduplicated property-manager alert if still unclaimed. General deadline-risk exceptions remain independent; the response window cannot authorize a late or infeasible assignment. Live scheduling, handoff commands, messaging and timer delivery remain disabled during local setup. The existing whole-day UNAVAILABLE replan is not an implementation of this task-level handoff.

Implements FR-04–FR-11 and FR-14–FR-19. All proposed defaults are subject to the approvals in [open questions](OPEN_QUESTIONS.md).

## Inputs and normalization

Solve by operational service date using UTC minute offsets from that date's local midnight instant. Input includes candidate tasks (release/deadline/duration/eligibility/location), cleaners (working intervals, dated start coordinates, limits, unavailable intervals), existing whole-day routes, adjacent-day occupied intervals, locks, completed work, policy version and routing matrix revision. Rounding: releases/travel/durations round up to minutes; deadlines/shift ends round down. Independently verify final instants at full precision before publication.

Planning candidates include released unfinished work on each feasible day up to the earlier of checkout plus 48 elapsed hours and the next check-in minus the readiness buffer. Include overdue work as exceptions, never as feasible candidates. No-arrival work has the 48-hour deadline. A manager target can only tighten this limit. Legacy null or later deadlines are capped at read/solve time until recomputation persists the new rule. A task may be served on one selected date only across rolling planning runs. Existing published assignments on other dates remain occupied unless a transactional revision explicitly moves them.

Duration precedence: validated property override, then approved bedroom mapping; otherwise unschedulable. Individual max-jobs/service-minutes/duty-minutes and working hours must be configured before a cleaner is routable. Duty includes service, travel and scheduled breaks. Manager-approved return-to-base policy determines end node; proposed default is no mandatory return, requiring explicit approval. Never assume an unknown daily start. Break intervals are unavailable blocks with travel feasible around them.

## Travel

Use Google Maps Routes API through `RoutingService`; do not assume current endpoint parameters before implementation verification. Use asymmetric durations and conservative departure-time buckets (proposed 30-minute buckets). For each used leg, use a conservative upper bound over buckets it can traverse, or iterate bucket selection then revalidate. Include start-to-first-job travel and approved end leg. Cache with location hash, mode, departure bucket, provider/version and TTL; preserve provenance in each run. Unreachable/missing/stale legs cannot be used. If routing provider is down, use only valid permitted cached durations or produce an exception. No straight-line distance substitutions in production without manager-approved validated travel bounds.

## Model

Use OR-Tools RoutingModel for vehicle routes with time/capacity dimensions or CP-SAT intervals with explicit successor/travel constraints when breaks/locks require it. One cleaner vehicle per permitted work interval with shared daily counters across intervals. Each task is optional only through an explicit uncovered decision; at most one vehicle/date serves it. For assigned task i:

- `start_i >= checkout_i`, `finish_i = start_i + duration_i`, `finish_i <= readiness_deadline_i` (or explicit target).
- First job starts after cleaner work start plus travel from dated origin.
- Successor j starts no earlier than finish i plus travel(i,j); all service/travel respects working intervals and breaks.
- Cleaner eligible for property and available for whole route; max jobs, service minutes, duty minutes enforced over whole local day, including earlier fixed work.
- Locked assignment retains cleaner, start/end and service date. Completed assignments are immutable occupied history; not candidates for reassignment. An in-progress/possibly-started job is pinned until manager resolves.
- Travel and service cannot overlap another fixed or planned assignment. Reject inconsistent input locks before solving and open `LOCK_CONFLICT`.

## Objective order and solver result

Use sequential lexicographic optimization; do not use arbitrary weights that could trade feasibility for travel:

1. Rank jobs by same-day turnover first, earliest actual next check-in second, earliest original checkout third. Same-day compares checkout and next check-in dates in America/New_York; missing next check-in sorts after known arrivals.
2. Sequentially maximize covered jobs in each priority group, fixing proven outcomes before lower groups. Then maximize total coverage and favor earlier starts by priority where feasible.
3. Fix preceding outcomes and minimize route travel seconds.
4. Fix preceding outcomes and minimize changes to acknowledged assignments (cleaner change first, then time shift minutes).
5. Fix preceding outcomes and minimize spread of utilization (`duty / configured capacity`) across participating cleaners.

Manager-directed priority order (2026-11-12) supersedes the earlier coverage-first objective: additional lower priority jobs cannot displace feasible higher priority coverage merely to increase count or shorten travel. Hard constraints and protected existing assignments remain binding; priority does not force an impossible chronological order. PRD intentionally ranks travel ahead of acknowledgment stability; all changed cleaners still require reacceptance. Time limit proposed 30 seconds total; allocate bounded stages. If an earlier stage times out, retain its best feasible result, label `feasible_not_proven_optimal`, do not claim a later-stage optimum. Use deterministic seed/tie-break IDs in tests. Record objective tuple, solver status/version, elapsed time, uncovered reason codes and input fingerprint. No solver solution means no publication; a feasible partial solution includes uncovered exceptions rather than fabricated assignments.

## Validation and publication

A separate domain validator recomputes constraints from the persisted candidate and current state, including every travel leg, locks, caps and deadlines. Under a per-service-date advisory lock plus row locks, compare reservation/availability/lock/rate/policy/instruction versions and routing validity. Stale preview returns 409. Acquire multiple date locks in ascending order for a move. Update assignments and outbox atomically. Preview can be run by manager; proposed dispatch policy permits scheduler service identity to publish a current validated preview only with approved policy and healthy inputs. Manager locks remain binding in both modes.

Publication may contain a feasible subset; every uncovered job gets one open exception with reason `NO_CAPACITY`, `NO_LOCATION`, `NO_ROUTE`, `DEADLINE_IMPOSSIBLE`, `INELIGIBLE`, `LOCK_CONFLICT` or `MISSING_DURATION`. Do not allow subset publication to hide critical uncovered work. Show unresolved count prominently and escalate immediately.

## Replanning and deadlines

Short-notice readiness (2026-11-12): consider remaining feasible work today before planning tomorrow, so unassigned vacant houses can use available checkout-day capacity. Confirmed same-day turnovers and check-in ordering remain first. This best-effort earlier service does not shorten hard deadlines, invent arrival times, reserve a statistical percentage of capacity or move assignments from another published service date. Preserve fixed daily work and independently validate any additional jobs before notifying cleaners.

Night-before delivery revision (2026-11-12): prepare feasible assignments immediately but hold initial daily emails until 20:00 America/New_York on the preceding date. Catch up missed delivery for remaining feasible work. This supersedes the earlier 18:00 examples and immediate-onboarding schedule emails. Imported reservation extensions may invalidate unlocked future assignments; remove obsolete instructions and promptly send an affected previously notified cleaner a factual change notice, even if no houses remain for the old day. Preserve locked and started work for manager review. Publish any replacement plan only after independent feasibility validation; no replacement time is guessed. Unchanged polls must not create duplicate mail, and an uncertain submission cannot be blindly retried.

Cleaner acceptance (2026-11-12): a changed automatic schedule revision alone does not require manager approval. Before a new acceptance, independently validate the cleaner's existing daily route with fixed assignment identities and windows against current source, availability, route data and published-day requirements. Include completed jobs' occupied time, preserve hard constraints and reject overlapping occupancy by another cleaner at the house. Successful acceptance leaves the global automatic marker untouched; unrelated uncovered work is not certified current. Pinned-route travel uses each previous house as the next leg's origin, rather than repeatedly charging travel from the cleaner's starting address.

UNAVAILABLE marks future availability excluded; cancel/supersede affected unstarted unlocked assignments, solve remaining work and send removed/new assignment notices with per-cleaner version changes. Locked or possibly started work becomes blocked with manager exception, no silent reassignment. Missing acknowledgment alone triggers reminders/escalation. If later approved to permit that reassignment, use a versioned explicit policy and repeat feasibility/notification checks.

For last-minute booking/change, recompute both departure task and predecessor deadline; cancel obsolete unstarted work, retain history, and replan only mutable work. If booking creates a past release/deadline contradiction, escalate and never move time backward. Monitor predicted finish against deadline and completion absence at deadline; actual late completion remains recorded as late. A schedule is a prediction; live completion delays trigger fresh feasibility review.

## Worked synthetic example

Jobs T-A and T-B both release 10:00 and must finish 15:30; each needs 120 minutes. Cleaner C-A works 09:00–16:00, capacity two jobs/300 service minutes/420 duty minutes. Start-to-A 30 minutes, A-to-B 20, B-to-end 0 under approved no-return policy. A 10:00–12:00 then B 12:20–14:20 is feasible. If B duration becomes 240 minutes, C-A service cap and deadline fail; use another eligible available cleaner or expose B uncovered. A manager lock cannot make the impossible route publishable.


## M8 bounded optimization implementation (2026-11-07)

2026-11-12 update: the coverage-first stages described historically below are superseded by the manager-directed priority order above. Both coverage allocation and bounded circuit routing fix priority-group coverage sequentially. The deterministic fallback orders jobs identically, and candidate selection compares priority-group coverage, then start times by priority, then travel. Actual next check-in and original checkout are distinct from capped cleaning deadlines and the current-time-clamped release. Existing published assignments remain protected during additive automatic planning. Acknowledgment stability and utilization optimization remain incomplete; priority optimality is not claimed for bounded or greedy results.

The full model above remains authoritative. The current implementation adds
OR-Tools CP-SAT optional circuits with successor travel/time constraints for
up to 12 jobs and 4 cleaners, capped at two seconds within the remaining solve
budget. It fixes proven coverage before minimizing travel and independently
validates candidates before choosing them over the deterministic fallback.
Travel can wait on either side of a break, but cannot overlap it. The independent
validator also rejects omitted locks. Larger inputs keep coverage plus greedy
sequencing. All outputs remain `feasible_not_proven_optimal`, because critical
priority, acknowledgment stability and utilization stages are incomplete.

Rationale: [open-source evaluation](OPEN_SOURCE_EVALUATION.md) measured better
small-plan coverage/travel using our mandated existing engine, with no new
service or dependency. This is a bounded M8 improvement, not completion of the
five-stage objective, production routing or the publication/override gaps.

2026-11-07 rationale: manager-directed 48-hour turnover limit supersedes indefinite pending tasks. Carryover tasks may enter later daily runs, but a live assignment on another date excludes the task from that run. Every selected-day shift still bounds service; independent final validation remains mandatory. The guarded local loop prepares next-day plans and records mock schedules only at/after 18:00 Eastern; the fixed-clock demo exposes explicit evening simulation. Durable production scheduling remains an M8 gate.

Local static-baseline mode: only the guarded demo may load the saved driving matrix described in [DRIVING_BASELINE.md](DRIVING_BASELINE.md). Use its directed values in both solver and validator, including the explicitly selected public starting point. Unknown legs are unavailable and cannot fall back to synthetic times. Preview revisions include a baseline identity; publication rejects a different identity. Source capture time/provenance are retained instead of renewed live-traffic freshness. Production routing remains unverified/disabled.

## Confirmed two-hour cleaning default (2026-11-11)

The manager replaced bedroom-derived duration assumptions with 120 minutes for every house unless an explicit positive property override exists. This applies to current and future houses, including five or more bedrooms, and does not require entering one duration per house or approving a bedroom table. Clearing an override restores 120 minutes. Task generation and setup readiness share this rule; existing open tasks are recomputed through the normal versioned/audited path and missing-duration exceptions close. Completed tasks return unchanged before recalculation. Historical assignment snapshots remain unchanged; schedule freshness/feasibility/publication safeguards still apply.

The legacy policy duration_minutes_by_bedrooms field remains accepted for wire/storage compatibility but no longer controls duration selection. It does not approve unrelated scheduling rules or enable dispatch. This section supersedes older bedroom-default references in the scheduling contract.

## Bedroom estimate refinement (2026-11-11)

The manager superseded the uniform two-hour default with bedroom-based estimates after cleaner guidance: one-bedroom apartment 90 minutes and three-bedroom home 150 minutes, using the upper reported ranges. The shared rule is 60 + 30 × bedrooms: studio 60, two bedrooms 120, four 180, five 210 and continuing 30-minute increments. Other sizes are provisional extrapolations, not measured guarantees; bathrooms, layout and guest condition remain reasons for a positive house-specific override. Explicit overrides always win and clearing one restores this bedroom estimate. Existing completed-task/assignment history is preserved; source bedroom changes update unfinished task estimates through normal audit/version/freshness paths. No policy approval, scheduling publication or messaging is enabled.

The old policy bedroom map remains accepted for wire compatibility, but the shared confirmed rule is authoritative. The legacy setup default_cleaning_minutes:120 field is retained only as compatibility metadata, not a universal duration. Property reads return effective/default duration fields and setup provides example bedroom values 0..5; clients should use those fields rather than reproduce the formula.

Local publication review guarantee (2026-11-11): manual publication deserializes the saved candidate plan rather than solving again. It checks complete/disjoint current task coverage, matching uncovered exceptions, current policy including effective time, source freshness,current OSRM binding identity and full input fingerprint. Independent validation checks the exact reviewed placements against fresh jobs/shifts/travel and protected locks. Cached local routes cannot fall back to synthetic estimates. Automatic clock dispatch remains mock-only. Blank local instruction snapshots now use the workspace field encryption key.

Cleaner handoff (2026-11-12): open/expired offer tasks are excluded from automaticallocation until a cleaner explicitly claims. Claims pin the claimant’s existing jobs and preserve all other cleaner assignments. Completed jobs consume through the later of plannedend/actualcompletion; started unfinished work blocks further claims rather than assuming an actual finish. Targethouseoverlap checks also treat anotherstartedunfinished visit as busy. Feasibility is independently validated before assignment; no manager lock or existing plannedtime moves. New schedule versions retain old assignment provenance,so emails render all valid livecleaner/day jobs across version IDs. Portal mutations advance automatic markers only if the marker matched the prior revision/run,never clearing unrelated stale inputs.
