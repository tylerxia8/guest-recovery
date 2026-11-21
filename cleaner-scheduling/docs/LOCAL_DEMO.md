# Local synthetic demo

**Demo entry:** select **Connect to Hospitable** to open the manager dashboard using the fictional local account. There is no role picker or provider login. A connection label explicitly says it is simulated; live access is still unavailable. Developer role coverage uses test sessions rather than manager-facing controls. This supersedes older role-selector walkthrough instructions below.


## Current manager workflow (2026-11-09)

1. **Hospitable:** review the simulated source for houses and bookings together. A future verified connection should supply both, without duplicate entry. Live account entitlement and field coverage are still unverified.
2. **Cleaners:** confirm eligible houses, individual capacity, hours, starting location and consent. Save regular weekly Eastern hours once; use the optional date-specific editor for exceptions.
3. **Run and view:** start demo automation once from the dashboard or setup guide. Open **Tomorrow** to see all next-day houses, cleaner assignments, driving time, deadlines and cleaning status; filter by cleaner. Supervise missing assignments, late work, source health and replies from the dashboard. Use **Review or adjust assignments** and **Review exceptions** when intervention is needed.

Only missing cleaning times require attention; approved bedroom defaults count as ready. **Review cleaning times** on the Hospitable screen opens optional overrides without editing source bedroom information. Manual house creation and booking simulation are collapsed **Demo tools** and do not represent routine manager data entry. Earlier walkthrough details below describe the underlying controls; these three steps supersede their old navigation/order.


This is the M8 local-demo subset, not production readiness. It uses the real M1–M7 domain services with fictional properties, cleaners, reservations and rates. The business clock is frozen at **November 10, 2026, 1 PM America/New_York**. JWT expiration still uses real time. No external provider, carrier SMS or bank transfer is available.

## Windows prerequisites

- Python 3.12 and uv; run `uv sync --frozen --all-groups` from `backend`.
- Node 22 on PATH; run `npm ci` from `frontend`.
- PostgreSQL 17 installed at `C:\Program Files\PostgreSQL\17\bin`, or set `DEMO_POSTGRES_BIN` to its bin folder.
- WSL distribution `Ubuntu` with `redis-server` installed. The launcher does not install software or start Docker.

From the repository root:

```powershell
backend\.venv\Scripts\python.exe infra\local\demo.py
```

Open **http://127.0.0.1:3108** and select **Enter demo** as manager. Keep the launcher terminal open. **Ctrl+C** stops the API, frontend, private PostgreSQL cluster, and private Redis process. You can also stop it from another repository-root terminal:

```powershell
backend\.venv\Scripts\python.exe infra\local\demo.py --stop
```

Logs and persistent demo data are in gitignored `.local-demo/`. Keep at least 1 GB of free disk space for normal development and test runs.

To restore the sample scenario, stop the demo and run:

```powershell
backend\.venv\Scripts\python.exe infra\local\demo.py --reset
```

Reset recreates only the literal `cleaning_demo` database inside the launcher's private cluster. It never truncates another configured database. Schema migrations remain unchanged. The launcher refuses occupied ports instead of stopping existing applications. Ports: frontend 3108, API 8108, PostgreSQL 55439, Redis 6398/database 7. It reuses the seeded database between launches, and requires reset after an interrupted first seed.

The private PostgreSQL cluster uses local trust authentication and contains synthetic data only. The API and frontend listen on loopback. Do not tunnel, publish, or bind this demonstration to a network interface. Any local caller can select a fictional manager/owner/viewer; this is deliberately not production authentication. The mock signing secret is generated into `.local-demo/secret` and remains server-side. Demo APIs are absent from the normal `app.main` application. The demo factory requires explicit `LOCAL_DEMO=1`, development/test, all mock providers, no provider credentials, both production flags off, loopback `cleaning_demo` database/migration targets, and loopback Redis database 7.

## Five-minute walkthrough

1. **Home:** Enter as manager, then open the operational dashboard. Review November 10's named assignments, completed work and explicit acceptance. Pending deadlines remain visible. Tomorrow includes follow-on departure tasks without invented deadlines.
2. **Schedules:** Load November 10. Simulate `CONFIRM` for an unfinished assignment. Review the refreshed status. `DONE` records eligible completion at the fixed demo clock; the domain handler rejects future work. `UNAVAILABLE` exercises the existing replan/exception behavior; it may leave uncovered work that requires manager attention.
3. Select **November 11** and **Generate preview**. Review property/cleaner names, times, deterministic mock travel, and uncovered reasons. **Publish validated schedule** runs independent feasibility and fingerprint/version checks. A conflict requires a fresh load/preview.
4. **Record mock schedule messages** creates recording-gateway facts only. Accepted mock messages and explicit cleaner acknowledgment remain separate facts. Reload the dashboard to review changes.
5. **Dashboard:** Review cleaning deadlines, assigned cleaners and completion status.
6. Switch to viewer to demonstrate read-only schedules and absent write controls. Backend RBAC independently denies writes.

## Verification

The regular Pytest suite includes isolation, default-app route absence, viewer denial, reply idempotency and conflict tests. The demo browser tour signs in, reviews named work, confirms a fictional assignment, publishes tomorrow, records mock messages and checks viewer controls. CI seeds a separate `cleaning_demo` database and runs this tour after the regular browser suite.

Against a running demo on this Windows machine:

```powershell
$env:LOCAL_DEMO='1'
$env:PLAYWRIGHT_PORT='3108'
$env:PLAYWRIGHT_REUSE_SERVER='1'
$env:CLEANING_API_ORIGIN='http://127.0.0.1:8108'
cd frontend
npx playwright test tests/demo.spec.ts
```

The tour mutates synthetic data; reset afterwards. `PLAYWRIGHT_REUSE_SERVER` is opt-in; CI continues to start its own frontend.

## Remaining release limits

M8 remains in progress. Full multi-day/rolling optimization, all manual override hard constraints, unchanged-cleaner version preservation, managed authentication, verified Hospitable/Twilio/Maps adapters, restore/kill-switch drills, business policy and consent approvals, and a measured production pilot remain open. This UI does not expose manual override controls. Scheduling uses the inherited coverage objective and deterministic sequencing; it does not claim the full five-stage product objective. No migration or production integration was added.


## Guided setup and simulated account

Open **Setup** to configure fictional houses, cleaners, eligible houses, individual
limits and dated Eastern working hours. Use **Reservations** for the fictional
`Charlotte Demo Properties` proxy account and timeline. **Start demo automation**
runs a server-side 30-second next-day planning/publication/recording loop while the
local API remains open. **Pause demo automation** stops subsequent cycles; errors
and API restarts also pause it. The dashboard follows the frozen business day and
refreshes every 30 seconds, with a manual refresh and explicit stale-read errors.
This is accelerated synthetic operation, not real 6 PM dispatch or a durable live
scheduler. [Full manager walkthrough and remaining gaps](MANAGER_SETUP.md).

## Optional southeast Charlotte public-record examples

At the manager's request, a local workspace may replace the 100 generic house names with public residential parcel addresses from southeast Charlotte. This is a presentation overlay, not a Hospitable import or evidence of rental status, management authority, occupancy, or rental licensing. No owner or occupant data is collected.

The optional `.local-demo/housing.json` file contains a source URL, retrieval timestamp, sample geography and per-property public address, property type, tax-assessment year and assessed value. It remains gitignored alongside the local database and rollback snapshot. The default seed and CI remain fully synthetic; resetting the database removes the local name substitutions and the context file should then be removed or regenerated for the new IDs.

Only the guarded demo app exposes `GET /v1/demo/housing`, to managers/owners. The regular app has no such route. Property cards display the source and distinguish historical assessments from current sale prices. Bedrooms and cleaning durations remain explicit demo assumptions because this dataset does not verify them. Routing coordinates, bookings and work schedules remain mock; public addresses do not turn on geocoding or external dispatch.

Verified source on 2026-11-07: City of Charlotte ArcGIS **Residential Parcel Revaluation and Tax Change**, layer 0: https://services.arcgis.com/9Nl857LBlQVyzq54/arcgis/rest/services/Residential_Parcel_Revaluation_and_Tax_Change/FeatureServer/0 . The current sample uses Charlotte residential Single-Family or Condo/Townhome parcels intersecting longitude -80.81 to -80.72, latitude 35.08 to 35.16. The service returns 2023 tax revaluation data; do not present it as current market value. No individual address is committed.

## Daily cleaning board and evening tour

The dashboard shows each selected day and the following day with house, checkout/release, hard deadline, current cleaner/planned time, explicit acceptance and cleaned/completed time. Filter by unfinished, unassigned or cleaned work. Unassigned work remains visible through its feasible window; overdue houses remain in a separate persistent action queue. Historical/superseded assignments are excluded from counts.

The manager-directed hard deadline is now checkout + 48 elapsed hours or next check-in minus the approved readiness buffer, whichever is earlier. No-arrival tasks no longer wait indefinitely. Existing rows are capped on dashboard/solve reads; normal task recomputation persists the new deadline with version/audit/outbox updates. Completed/payment history and migration files are preserved. Before shared rollout, review existing locks/published plans against tighter deadlines; incompatible locks require resolution rather than silent reassignment.

Activate local automation to prepare tomorrow, then choose **Simulate 6 PM schedule delivery**. This records mock schedules with house names, planned start/end, deadline, CONFIRM and DONE instructions. Repeating delivery preserves version/part idempotency. The automatic loop only records at/after 18:00 Eastern business time; because the demo clock is frozen at 13:00, the explicit simulation is needed for the evening tour. No carrier SMS, wall-clock production scheduler or durable worker is enabled. Current loop plans only next day; intraday automated replan and multi-day global optimization remain open M8 work.

## Saved driving times

The current Charlotte workspace has a one-time saved OSRM driving matrix for all 102 example houses and a manager-approved public staging point. The launcher loads it explicitly; no online routing calls run during scheduling. Dashboard rows display the drive before each job and its source. Earlier started/completed assignments retain their synthetic travel facts; future unlocked work is recalculated using the saved baseline. Missing new houses/origins block affected routes until a reviewed capture. Read [driving baseline](DRIVING_BASELINE.md) for source attribution, capture/refresh/reset instructions, static-traffic limitations and verification.


Cleaner form update: enter the phone number normally, including spaces, brackets
or dashes. A US number can omit +1. Set working hours and breaks instead of job,
cleaning-minute and duty-minute limits. Type the weekly starting address with
city/state/ZIP. A resolved Charlotte address saves driving estimates for reuse;
unknown/out-of-area routes stay flagged until corrected. No external texts are
sent. Date-specific exceptions still override weekly hours.


To remove someone who no longer works for you, open Cleaners and use Remove
cleaner beside their name in Your cleaners. Confirm removal or choose Keep
cleaner. Past work stays in history. Their future unlocked work becomes available
for rescheduling; running demo automation replans tomorrow. Review exceptions for
uncovered work and locked/already-started assignments requiring manager action.
The former cleaner?s access links and further schedule messages stop working.
