# Open-source evaluation and adopted improvement

Reviewed 2026-11-07 against PRD.md and the original coordination problems. This
is a source review plus a measured scheduling experiment, not a benchmark of
all listed engines or evidence of production readiness.

## Decisions

| Problem / candidate | Evidence and license | Decision |
| --- | --- | --- |
| Assignment and travel sequencing: Google OR-Tools | [Official CP-SAT scheduling guide](https://github.com/google/or-tools/blob/100f66e6242ab8bf8d32feb8f3bf086db66ae2b5/ortools/sat/docs/scheduling.md), especially circuit ranking and transition times; Apache-2.0 | Adopt the optional circuit/successor modeling technique using our existing OR-Tools 9.15 dependency. Joint assignment, order and time constraints improve small synthetic plans. No new dependency or copied source files. |
| Routing optimization: PyVRP | [Reviewed README](https://github.com/PyVRP/PyVRP/blob/3c3ecced87dd652f4c7ca73025ce15379866d42e/README.md); MIT | Supports heterogeneous vehicles, windows, releases and service durations. README labels automatic regulatory break planning Enterprise. No measured advantage for our exact locks, daily caps and objective hierarchy; switching also conflicts with the PRD OR-Tools requirement. Do not substitute. |
| Routing optimization: VROOM | [Reviewed README](https://github.com/VROOM-Project/vroom/blob/07be776fc20b8d6c85d9f78797e38a9f4e44ec44/README.md); BSD-2-Clause | Strong alternative with windows, skills, breaks and custom matrices. Requires another engine/integration and evidence for our lock and objective semantics. Not benchmarked here; no claim it is inferior. Retain OR-Tools. |
| Road travel matrices: OSRM | [Reviewed README](https://github.com/Project-OSRM/osrm-backend/blob/746c97c1c8ef7ebeb7acb9b986cf6c4e04f82caf/README.md); BSD-2-Clause | Table service could replace paid matrix calls in a separately approved adapter. Static road-network durations do not establish the conservative departure-bucket traffic bounds required by our contract. No local Charlotte routing data or accuracy comparison; do not enable or use a public demo endpoint. |
| Road travel matrices: Valhalla | [Reviewed README](https://github.com/valhalla/valhalla/blob/fc3c613f31f77aac77823c6d3494cbf691c177b8/README.md), [MIT license](https://github.com/valhalla/valhalla/blob/fc3c613f31f77aac77823c6d3494cbf691c177b8/COPYING) | Offers road routing and matrices. Hosting, map-data licensing/updates, traffic availability and validated conservative travel bounds remain unproven. Keep mock routing and existing production gates. |
| Durable dispatch/retries: Temporal | [Reviewed README](https://github.com/temporalio/temporal/blob/47409d6ee4a5f6b84e63d4e352bcb18e84c007c0/README.md); MIT | Durable execution is relevant, but replacing current transactional outbox/Celery adds a service and migration burden. No observed reliability/latency advantage demonstrated. Retain Celery and PostgreSQL idempotency. |
| Background jobs: Celery | [Reviewed license](https://github.com/celery/celery/blob/768e476b5397fb4efe6439fdd818e47cd5c3a90a/LICENSE); BSD-3-Clause code, separate CC-BY-SA documentation license | Already incorporated. Retain the recording gateway, idempotent dispatch and explicit acceptance state. A workflow framework cannot establish carrier delivery or cleaner acceptance. |
| Reservation sync, task derivation, payments and manager workflows | Existing FastAPI, SQLAlchemy/PostgreSQL, Next.js and Playwright already supply open-source foundations | Business-specific source revisions, deadlines, compensation snapshots and approval rules still require our domain code. No reviewed replacement demonstrates better behavior. Do not invent Hospitable compatibility or add payment-transfer capabilities. |

Repository metadata and linked first-party source files were retrieved directly
from GitHub; no third-party benchmark claims were treated as local evidence.
The license descriptions cover the reviewed projects' code, not all their
transitive dependencies, map datasets or hosted services. Adoption adds no new
package or service; the existing dependency lock remains unchanged.

## What changed

The former CP-SAT model selected cleaner/job counts without routing time;
deadline-sorted greedy sequencing then chose actual routes. Optional circuits
now jointly choose assignment, predecessor, start time, travel and service for
at most **12 jobs and 4 cleaners**, within the remaining policy time budget and
a **2-second added search/model budget**. The size cutoff bounds model-building
work; this is not a strict real-time deadline if a provider callback blocks.
Missing routes are omitted, eligibility and exact locks are enforced, and daily
jobs/service/duty limits include elapsed breaks. Travel may wait before or after
a break but cannot occupy it. No physical return-to-origin leg is introduced.

First maximize coverage; only after proving that stage, fix its count and
minimize travel. Adopt only an independently validated candidate that improves
coverage or preserves coverage with no greater travel, or replaces an invalid
fallback with a valid plan. A timeout, infeasible
model or rejected candidate retains the prior fallback; inconsistent locks still
fail independent validation and cannot be silently dropped. Larger plans retain
the previous coverage-plus-sequencing algorithm. The fallback and validator also
now ensure a travel leg can fit around breaks; the validator rejects omitted
locked jobs. Every result is labeled `feasible_not_proven_optimal`: critical-job
priority, acknowledgment stability and utilization stages remain unfinished.

This bounded approach was selected because it gives demonstrable local-demo
quality improvements without pretending to solve the full-scale PRD objective.
Publication still re-solves, override feasibility and multi-day planning remain
M8 review gaps. This change does not clear those gates.

## Reproducible evidence

From `backend`, run `.venv/Scripts/python.exe -m scripts.benchmark_routes` (or
`uv run python -m scripts.benchmark_routes`). Seed 204; 12 four-job/one-cleaner
instances plus a 100-job/10-cleaner fixture, all fictional, directed travel.
The baseline uses the former coverage model and both greedy variants. No breaks
in these benchmarks, so the fallback break correction does not affect the
comparison. Timings are one Windows/Python 3.12 run, not p95 or production SLAs.

| Fixture | Baseline | New | Interpretation |
| --- | --- | --- | --- |
| 12 small instances, total covered | 35 | 40 | Five additional feasible jobs; 14.3% in this synthetic set only |
| Seven instances with unchanged coverage, total travel | 357 min | 200 min | 44.0% less travel at equal coverage |
| Small-instance solve time | 0.82-4.35 ms | 15-47 ms | Higher computation cost buys route quality |
| 100 jobs / 10 cleaners | 70 covered, 70 travel min, 116 ms | 70 covered, 70 travel min, 188 ms | Same fallback and solution quality; timing variation is not a speed improvement |

All benchmark results passed independent validation. Separate tests compare the
new planner with exhaustive permutation enumeration on all 12 small instances,
and cover greedy ordering failure (1 to 2 jobs), filling a gap before a lock,
missing origin route reachable via another task, travel around breaks, waiting
at the destination, cleaner eligibility/caps and independent dropped-lock
rejection. Full regression and CI evidence is recorded in the implementation
plan. No production provider accuracy, traffic, live SMS, restore drill or pilot
coordination-time reduction was measured.
