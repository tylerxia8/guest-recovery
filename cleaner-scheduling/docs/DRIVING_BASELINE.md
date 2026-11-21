# Saved Charlotte demo driving baseline

The manager requested online driving estimates captured once and reused in allocation. On 2026-11-07 the manager explicitly selected **one public demo staging location** for cleaner first-job travel. The local example uses South County Regional Library, identified through OpenStreetMap/Nominatim; it is an assumed demo start, not a cleaner home or a business affiliation.

## Sources and measured capture

- Parcel centers: City of Charlotte [Residential Parcel Revaluation and Tax Change, layer 0](https://services.arcgis.com/9Nl857LBlQVyzq54/arcgis/rest/services/Residential_Parcel_Revaluation_and_Tax_Change/FeatureServer/0), matching the saved housing addresses. The verified query returns `centroid` in EPSG:4326 using `returnCentroid=true`, `outSR=4326`.
- Staging point: [OpenStreetMap venue](https://www.openstreetmap.org/way/632007229), resolved with a single Nominatim search using an identifying application User-Agent. [Nominatim policy](https://operations.osmfoundation.org/policies/nominatim/).
- Driving estimates: OSRM [Table API](https://project-osrm.org/docs/v5.24.0/api/#table-service), verified at `https://router.project-osrm.org/table/v1/driving/`. The API documents durations in seconds for fastest road routes, with separate source/destination indexes. We did not request `fallback_speed`; null cells remain unavailable.
- [Public demo service limits](https://github.com/Project-OSRM/osrm-backend/wiki/Demo-server) restrict usage to reasonable non-commercial demonstrations, at most one request per second, with no uptime/data-update guarantees. This was a one-time local prototype evaluation, using nine bounded matrix blocks, sequentially more than one second apart. The public endpoint is not integrated into the running product or selected as a commercial production service. Production/pilot routing requires an appropriately authorized hosted or self-hosted source and entitlement/terms review.
- Attribution: routing by [OSRM](https://project-osrm.org/); map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), ODbL. Attribution is displayed in the demo dashboard.

The captured matrix contains **102 properties, 10,302 directed property-to-property legs**, and the public staging point. All cells were road-routable. Property-to-property estimates ranged from 1.0 to 23.8 minutes, median 11.2 minutes; 4,980 unordered property pairs have different times by direction. Maximum parcel/staging-to-road snap was 73.92 meters, below the capture's 150-meter review threshold. This is not verification of a driveway or entrance. Static estimates exclude live traffic, parking, walking and property-access time. They must not be presented as guaranteed arrival times.

## Capture, storage and activation

Run `backend/.venv/Scripts/python.exe infra/local/build_driving_baseline.py` only for this guarded local demo, after preparing the ignored `housing.json` and the selected venue's `staging-source.json` in `.local-demo`. The utility verifies the dedicated database/flags, matches housing IDs/names, reads existing fictional origin labels, fetches parcel centers, and captures 40-by-40 directed route blocks. Successful responses are saved by request hash for interrupted-run reuse. Durations round upward to seconds; distinct locations have a conservative minimum of 60 seconds. Solver/validator round travel upward to whole minutes.

The final file is written atomically to `.local-demo/driving-baseline.json`, with coordinates, house identity bindings, origin aliases, capture time, provenance and matrix. Raw source responses, public addresses and coordinates stay ignored by Git. Running the utility when the final file already exists makes **no online requests**. A deliberate reviewed refresh requires moving the old final file aside; unchanged raw queries continue to reuse their saved responses unless those are also deliberately retired.

The launcher explicitly sets `LOCAL_DEMO_ROUTING_BASELINE=1` when the saved file exists. Only the isolated demo factory loads it and checks property IDs and names; stale bindings or invalid files fail startup. The ordinary application and default synthetic CI fixtures never load it. Remove or regenerate the baseline and housing context after a local database reset; UUID bindings prevent accidental reuse for unrelated houses. New properties/origin labels not captured in the baseline have unavailable legs until reviewed capture, never synthetic or zero-time fallback.

## Scheduling and display

The demo injects the loaded matrix into its database sessions. Allocation and independent final validation use the same directed values for first-job and inter-property travel, sequence gaps, working hours/breaks, and duty limits. Both the preview fingerprint and publication checks include the baseline identity. An old preview cannot publish after a baseline replacement. A demo restart compares travel values and baseline identity as well as assignment times before deciding that a published plan is unchanged.

Routing snapshots retain source provenance and the original capture timestamp. The existing database requires expiry strictly after observation; static snapshots use capture + one microsecond, marking the live cache immediately expired rather than inventing renewed freshness. Explicit local static-baseline mode intentionally uses the saved matrix, not a live-cache TTL. Production remains disabled. No migration history is changed.

Dashboard rows show drive minutes, the previous stop (or public staging point), and whether the assignment used the saved baseline or an earlier synthetic estimate. Already-started/completed/locked work is not silently rewritten. Future unlocked work is replanned; historical assignments keep their original source and travel facts. Failed/unknown legs create uncovered work through existing feasibility checks. Existing 48-hour/arrival deadlines and cleaner limits continue to apply.

Tests: `tests/test_driving_baseline.py` covers asymmetry, missing legs, saved-response reuse without a second request, allocation/validation time gaps, snapshot/dashboard provenance, stale previews and stale house bindings. `frontend/tests/daily-cleaning.spec.ts` checks visible driving times and source labels with fictional data. Tests do not call external routing services.


## Typed cleaner starting addresses (2026-11-09)

Manager feedback supersedes the fixed staging selector: use a typed full starting
address. This is a bounded exception to the original capture-utility-only scope,
limited to the explicitly loaded local Charlotte baseline. Production adapters
remain unavailable. The API performs a one-time capture on manager save, never
from scheduling or validation. Exact matches to saved property names reuse the
existing matrix without online calls. Other addresses use the official US Census
[Geocoding Services API](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html)
locations/onelineaddress resource with Public_AR_Current and format=json (official
document and resource verified 2026-11-09). A single address match supplies x/y;
these are interpolated geographic estimates, not verified entrances. The local
prototype bounds capture to the Charlotte region. The existing documented OSRM
Table resource supplies up to 40 destinations per sequential request, separated
by at least 1.1 seconds. No fallback_speed; null legs remain unknown and snaps
beyond 150m require review. Public services have no uptime/traffic guarantees.

Only complete successful captures are reused; failures keep an explicit review
status and never substitute public staging or mock travel. Changing the address
or baseline triggers a new capture. Addresses are encrypted in ignored local
runtime data/database records and returned only to managers; neither private
addresses nor geocoder URLs/errors are logged or committed. Cached durations are
bound to the selected baseline identity and weekly version. Historical routing
snapshots remain immutable. Dashboard viewers see "Cleaner starting address".
Test sources and responses remain fictional; tests never call online services.

## Free local routing for the read-only account (2026-11-11)

The manager rejected paid API setup and authorized a free workaround. The isolated Hospitable workspace now opts into a self-hosted OSRM car/MLD graph, using North/South Carolina OpenStreetMap extracts from [Geofabrik](https://download.geofabrik.de/north-america/us.html). No public OSRM demo, Google account, API key or metered routing service is used. Existing computer resources run the service. Static road estimates exclude live traffic, parking, walking and entrance verification. OSRM attribution and OpenStreetMap contributor/license links appear in setup.

The pinned OSRM image is `ghcr.io/project-osrm/osrm-backend:v6.0.0@sha256:729461bcc9ae9e6aafa92c0f93db9b060a32e85d5e72092c01ae4a4a9f1eb564`; preparation uses `osmium-tool=1.18.0-1` on the pinned Python base. A buffered Charlotte crop includes the NC/SC border. Source SHA256 values, graph identity, profile, bbox and preparation time are recorded in ignored private state. Keep prior graph generations for rollback. Prepare graphs while the routing service is stopped and recreate it before restarting the API; changing the current symlink alone does not reload OSRM memory. The routing service has no published host port and suppresses request/coordinate logs.

Read-only Hospitable property coordinates are encrypted locally; street addresses, guests and access codes are not imported. Missing, invalid or out-of-region house locations block the local matrix. Directed durations come only from the private Table API with no fallback_speed. Null legs remain unknown; snaps beyond 150 meters require review. Graph/property identity changes invalidate the baseline, and cleaner saves verify current bindings under the publication lock.

Saved cleaner addresses use the free official [US Census geocoder](https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html), requiring exactly one in-region match. Only the lookup goes to Census; house/start coordinate routing stays local. This supersedes the earlier no-online-lookup preparation rule when LOCAL_FREE_ROUTING is explicitly enabled. The address remains encrypted; URLs, addresses and coordinates are omitted from logs/audit. Successful complete cleaner matrices bind to the baseline identity. Ambiguous/outside/unreachable results remain review statuses, never synthetic estimates. Initial source reconciliation captures existing saved addresses; subsequent unchanged successful submissions reuse saved routes. Scheduling, dispatch and upstream mutations remain disabled.
