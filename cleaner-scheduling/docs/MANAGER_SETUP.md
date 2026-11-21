# Manager setup and supervision

**Demo entry:** select **Connect to Hospitable** to open the manager dashboard using the fictional local account. There is no role picker or provider login. A connection label explicitly says it is simulated; live access is still unavailable. Developer role coverage uses test sessions rather than manager-facing controls. This supersedes older role-selector walkthrough instructions below.


## Current manager workflow (2026-11-09)

1. **Hospitable:** review the simulated source for houses and bookings together. A future verified connection should supply both, without duplicate entry. Live account entitlement and field coverage are still unverified.
2. **Cleaners:** confirm eligible houses, individual capacity, hours, starting location and consent. Save regular weekly Eastern hours once; use the optional date-specific editor for exceptions.
3. **Run and view:** start demo automation once from the dashboard or setup guide. Open **Tomorrow** to see all next-day houses, cleaner assignments, driving time, deadlines and cleaning status; filter by cleaner. Supervise missing assignments, late work, source health and replies from the dashboard. Use **Review or adjust assignments** and **Review exceptions** when intervention is needed.

Only missing cleaning times require attention; approved bedroom defaults count as ready. **Review cleaning times** on the Hospitable screen opens optional overrides without editing source bedroom information. Manual house creation and booking simulation are collapsed **Demo tools** and do not represent routine manager data entry. Earlier walkthrough details below describe the underlying controls; these three steps supersede their old navigation/order.


## Intended operating model

The manager configures houses, cleaning times/instructions, eligible cleaners,
weekly working calendars, individual starting points/caps and consent;
connects an authorized Hospitable account; approves operating policy; then
supervises automatic reservation reconciliation, feasible scheduling, dispatch,
acceptance, completion and exceptions from the dashboard. Normal days should
not require hand-building schedules. Exceptions, conflicting locks, uncertain
source data and possibly started work still require manager resolution.
Payments are outside the current scheduling-only scope.

This is the product direction requested on 2026-11-07. The local flow below
implements a synthetic demonstration, not production activation.

## Current local walkthrough

1. Open `http://127.0.0.1:3108`, choose manager, and enter the demo. Open **Setup**.
   The checklist reads actual stored properties, cleaner configuration, dated
   availability, current-rate presence, consent, policy and source status.
   These counts are configuration indicators, not a production-ready certificate.
2. **Properties:** add a fictional name, stable reservation property reference,
   bedroom count and explicit cleaning duration. Demo coordinates are fictional;
   do not enter real addresses. Cleaning-time edits check the stored version,
   lock the row, recalculate affected tasks and record an audit event. Property
   lists follow server cursors, including entries beyond the first 100.
3. **Cleaners:** add a fictional name/phone, individually supplied daily job,
   cleaning-minute and duty-minute limits, and eligible houses. New cleaners do
   not receive inferred consent, availability or compensation. Select the cleaner
   and working date, load the current version, then enter Eastern working hours,
   an optional break and a `synthetic:` starting point. Saving rejects concurrent
   stale versions and invalid/ambiguous/nonexistent DST times. The simple editor
   handles one shift and one break; it will not silently replace a complex day.
4. Record fictional consent evidence explicitly for the selected cleaner. A scenario
   policy is preapproved in the seeded demo; that is not production approval.
   No compensation setup is needed for scheduling.
5. **Hospitable:** the fictional account **Charlotte Demo Properties** represents
   the future Hospitable connection. No external account or credential is created.
   Its timeline shows stored canonical mock bookings, without guest details.
   Optionally add a synthetic departure/next-arrival pair for an active mock house
   with no overlapping stay. Use a future checkout; the readiness buffer still
   comes from the approved scenario policy. This writes mock bookings locally,
   not reservations in Hospitable. Turnover requests are idempotent and serialized.
6. On **Setup**, **Reservations** or **Dashboard**, select **Start demo automation**.
   The local API checks its simulated account every 30 seconds, reconciles mock
   records and previews/publishes the next business day's plan. Automatic mock SMS recording is gated until 8 PM Eastern; use **Simulate 8 PM schedule delivery** to demonstrate evening delivery with the fixed business clock.
   Repeated unchanged cycles preserve assignment IDs/versions and do not duplicate
   messages. New uncovered work still gets exceptions even if the placed route
   is unchanged. Feasibility, locks, freshness and transactional publication
   checks remain in force. Changes may require cleaner reacceptance.
7. Watch **Dashboard** for completion, acceptance, uncovered work, source health
   and exceptions. It follows the server's Eastern business date and refreshes
   every 30 seconds. A refresh failure labels retained information as potentially
   stale. **Refresh now** checks immediately; historical dates remain selectable.
   The source timeline has an explicit refresh button and a 100-stay/31-day display
   window. Cleaner replies can be simulated on **Schedules**.
8. **Pause demo automation** prevents subsequent cycles; an in-flight transaction
   can finish. API restart or a cycle error pauses the loop. The loop runs while
   the local API remains up, even if the browser closes. This is a single-process
   demonstration, not a durable production scheduler or a wall-clock 8 PM dispatch.
   The scenario business clock remains November 10, 2026 at 1 PM Eastern.

Viewer controls are read-only and API roles enforce writes. Changing the role
cookie cannot grant backend authority. All demo-only account/automation/turnover
routes are mounted exclusively by the guarded loopback demo app, never the normal
API. No live provider dispatch, real booking or transfer occurs.

## Remaining work before set-once unattended production

Full house/instruction and
cleaner/eligibility editing; verified Hospitable account entitlement, auth and
change/cancellation contracts; production routing bounds; full scheduling
objectives, multi-day replanning and exact persisted-preview publication;
verified SMS consent/sender setup; managed authentication; durable scheduling,
monitoring/alerts, backup restore, shadow operation and pilot approval remain open.
The setup checklist never enables live operation or clears these gates by itself.

No real Hospitable account is currently available. The manager explicitly chose
a proxy demonstration. Replace the mock through the existing adapter contracts
only after official documentation and authorized account tests establish the
integration behavior; do not copy the synthetic wire format into that adapter.


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

## Connected local setup (2026-11-10)

New cleaners now cover all current and future active houses automatically. Leave House exceptions empty unless a cleaner cannot service a particular house. Select a saved cleaner and use House exceptions to edit exclusions. Older saved allowlists are preserved until you deliberately save that editor, which explains the change to default-all eligibility. You no longer need to select all houses during cleaner creation.

The agreed future handoff flow is automatic initial allocation, an explicit one-house punt if necessary, another feasible cleaner's explicit acceptance, and a manager alert after 120 minutes without a claim. That handoff/messaging flow is not yet implemented or active in this local setup workspace.

Open `http://localhost:3011/setup` in the separate Hospitable workspace. Connect with the local session if needed, then open Cleaners. Add each cleaner's name, phone and optional maximum houses per day; houses are allowed by default unless excluded. Select the cleaner to save weekly Eastern working hours, breaks and starting address. Working hours also cap daily service and duty time. Phone and address fields are encrypted on the local server. No message or Hospitable update occurs. With free routing enabled, the submitted starting address is looked up through the US Census geocoder; road estimates are computed locally. Real consent collection and date-specific address editing are not enabled in this preparation step.

Use Properties to set local cleaning-time overrides. Bedroom information remains owned by Hospitable. These edits never change the source account. Preview generation stays disabled while driving times, scheduling policies and source exceptions remain unresolved; entering setup data does not approve policies or enable dispatch.

The former Google setup option is superseded by the free local routing setup below. No Google account, API key or billing activation is required.

The Hospitable tab now opens Daily operations: select an Eastern date, then Currently occupied, Check-ins, Check-outs, Same-day turnovers or Local cleaning tasks to see one focused list. Other dates label occupancy at start of day. Task records belong to this app, not Hospitable Tasks. Connection details & source tools contains manual import, setup links and the house directory. Source freshness remains visible; imported records do not establish complete occupancy coverage.

Use the previous-day and next-day arrows beside View date to move one day at a time, or select a date directly. Today returns to the current Eastern business date. Arrows stop at the supported date limits and remain usable while another date loads.

## Cleaner onboarding link

In workspace signup, enter cleaner names and emails, then choose **Create cleaners & finish**. The app saves the roster, queues private onboarding emails and opens the dashboard. Its invitation notice reports pending and provider-accepted messages, with delivery failures or uncertainty shown explicitly. No link copying is required. Duplicate completion requests do not automatically resend an initial invitation. A configured private email sender and `GMAIL_ONBOARDING_SEND_ENABLED=true` are required; this flag is independent of assignment automation and SMS. Local-preview URLs work only where that preview is reachable; externally hosted invitations require the configured HTTPS cleaner portal.

On Cleaners, select the languages each cleaner can read: English, Spanish and Portuguese. Multiple selections are allowed; leave them unchecked if unknown. Use **Languages** on an existing cleaner's roster row to edit their choices. Cleaners can also provide this information while saving their private onboarding form. Language choices are stored independently of schedule assignments.

Set **Preferred email language** for a cleaner, or leave it automatic to use a known readable language. Cleaners can choose English, Español or Português at the top of their onboarding and job pages. Onboarding saves that choice with their details; the job-page selector saves it for subsequent emails. All cleaner instructions, actions, feedback and date displays are translated; property names and addresses stay unchanged. When no readable language is known, new email communications contain all three languages. Existing private links continue to work, and explicit browser/page choices take precedence over profile defaults for display.

With local scheduling, verified free routes and assignment email automation enabled, finishing workspace setup begins remaining work today before tomorrow. Cleaners become eligible after saving their hours and starting location and obtaining verified driving routes. Available capacity can clean vacant houses on checkout day after confirmed check-in work, improving readiness for short-notice bookings. This does not move the hard 48-hour deadline or silently bring forward existing future assignments. Saving cleaner onboarding wakes planning; initial next-day emails wait until 8 PM Eastern, while newly assigned same-day work is sent promptly. Houses without a feasible cleaner remain visible for manager attention; no working hours or travel times are invented. Imported booking extensions trigger prompt corrections for affected previously emailed work, including a house removed from the day. Locked or started conflicts need manager review; replacement schedules must remain feasible.

In Cleaners, add or select a cleaner and choose Create onboarding link. Open cleaner preview to see their page. The cleaner selects regular working days, Eastern start/end times, an optional break for each day and their usual starting address, then saves. Manager: edit weekly hours remains available as a fallback. Links expire after seven days; Replace onboarding link disables the old one. Removing a cleaner also disables their links. Creating/copying a link sends no text and records no SMS consent.

The current Hospitable workspace is laptop-only. A localhost link cannot open on a cleaner’s phone; use this as a local preview until a reachable HTTPS deployment is configured. Do not expose the laptop’s local owner connection/session endpoint to make these links public. The link is private: it authorizes reading and editing that cleaner’s setup until expiry or replacement.

Simplified availability form: select working days, enter usual start/finish times once, optionally add a break and save a starting address. Different hours on some days opens individual-day fields; saved schedules with different hours automatically open in that mode. Cleaner saves finish with a confirmation and Edit my details action.

## Free routing setup (2026-11-11)

Google setup is no longer required for this local workspace. The free alternative uses a private OSRM service and downloaded OpenStreetMap roads; it has no API subscription and uses the existing computer. A free Census lookup locates cleaner starting addresses. Estimates have no live traffic and do not verify entrances. Review route failures rather than substitute fixed driving minutes. Setup reports current house and cleaner coverage. Starting-address edits reuse unchanged successful captures, or recapture when the address/road-map/house locations change. Scheduling and SMS remain separately disabled.

The private routing directory holds North/South Carolina PBF downloads from Geofabrik, prepared graph generations, source hashes and the current manifest. `python3 infra/local/prepare_free_routing.py` prepares the road graph from those downloads. Stop the private routing service before a deliberate rebuild; recreate it and restart the API after switching graphs. Existing graph generations remain available for rollback. Run `python3 infra/local/hospitable.py` to start the isolated workspace; it prepares a missing graph, backs up database/key and starts the private routing service alongside the app. No routing port is exposed on the host.

Every house now defaults to two hours, including future imports. You only need Properties for an exception: select a house and save a different number of minutes. Leave the override blank and save to return to two hours. Setup displays the default instead of asking you to configure each house.

Setup → Review locations now opens Locations, listing every active house and cleaner starting address together. Search by house/cleaner name or cleaner starting address. House map links open individually; manager access is required to read saved cleaner addresses. Counts show matching/total records and the full lists are not limited to the first100 houses.

Bedroom estimates replace the flat two-hour default: one bedroom 90 minutes, two 120, three 150; studios60 and each additional bedroom adds 30 minutes. Properties displays each house’s effective estimate. A saved override takes priority; blanking it restores the bedroom estimate. Adjust for unusual layout, bathrooms or condition when needed.

Dashboard now opens Today’s cleaning with one list of houses, actual assigned cleaners, deadlines and status. To clean is the default filter; Unassigned, Cleaned and All are available alongside name search. Current-day overdue work stays in the same list without duplicate task rows. The date picker and day arrows browse other service dates; Today follows the server’s Eastern business day. Assigned names only appear after an actual assignment exists. The dashboard refreshes every30 seconds and labels refresh failures; detailed health/demo tools are collapsed below.

## Fictional team and SMS preparation (2026-11-11)

The manager confirmed the original persona is fake. Eight demo cleaners are now saved with clearly labelled names and reserved non-working555-0100..0107 phone contacts. Four are part-time examples. Existing persona hours remain unchanged; the three new full-time examples use weekday08:30–17:00 with a12:30–13:00 break, and four part-time examples use weekday08:30–12:30. New examples share the existing saved demo starting address and have ready local routes. All schedules/starts remain editable. No SMS consent was fabricated and no text was sent.

Twilio is recommended for eventual real messaging. Create an account using your own verified email/personal phone, not a fictional cleaner contact. Current Twilio trials only support verified recipients and predefined content, so the app’s custom assignment texts need the appropriate account upgrade/sender registration and real consenting numbers before activation. New console users cannot create the older test credentials described in older tutorials. Keep the fictional team in local demo/recording mode. Provider credentials alone must not enable dispatch; public callbacks, durable workers, real consent/signature validation and schedule launch checks remain open.

Interim Gmail setup (2026-11-11): enable Google 2-Step Verification, then create an app password if permitted by the account. Run `python3 infra/local/configure_gmail.py` locally; the hidden prompt saves Gmail credentials atomically in ignored private-data/hospitable/gmail.json with owner-only permissions. Never use the normal Google password or paste credentials into chat. The local compose mount already includes this file location. On Cleaners, add actual email contacts and use Send test to myself after setup. The test sends only to the configured sender. Gmail configuration does not enable cleaner dispatch, SMS, schedule publication or Hospitable writes. Automatic assignment emails and a public cleaner portal remain future work. Official prerequisites: https://support.google.com/mail/answer/185833 .

On Cleaners, the owner Email card now offers Preview email for a sample assignment. Review the displayed recipient,subject and complete body, then explicitly click Send sample email. It uses Benito’s locally saved email and fictional cleaning details; no real assignment is created. Refreshing/retrying the same preview does not resubmit the message. Gmail acceptance does not prove inbox delivery or cleaner acceptance.

Real assignment emails: under Cleaners → Email → Assignment emails, choose the cleaning date and preview each cleaner’s published schedule. Save missing email contacts first. The preview shows the exact recipient and full house/address/time/deadline content. Send assignment email requires explicit action. No published assignments means there is nothing to send; local scheduling remains disabled until its separate launch work is complete. Gmail acceptance is distinct from inbox delivery, cleaner acceptance or completion.

Manual local scheduling: open Schedules, review the proposed rules and approve as owner. The approval applies the readiness buffer to existing imported tasks locally. Choose a cleaning date(default tomorrow Eastern), Generate preview, review assignments/uncovered work, then Publish validated schedule. Publication creates local assignments only. Review/send email separately under Cleaners. Automatic scheduling,email dispatch and SMS remain disabled.

Automatic email operation (2026-11-12): normal local manager workflow is Dashboard and Cleaners. The explicit automation flag is enabled in ignored private environment configuration. The running app prepares feasible assignments immediately and sends initial next-day schedules at 8 PM Eastern, with catch-up for remaining work after a missed run. Changes to previously emailed reservations prompt factual correction notices and validated replacement schedules where feasible. Missing emails are editable on Cleaners; unresolved conflicts and delivery uncertainty need review. No manual preview/publication/email buttons are required. The app must remain running; Docker services restart unless explicitly stopped. Provider acceptance is not proof of inbox delivery or acknowledgment.

Cleaner portal and handoffs (2026-11-12): schedule emails provide a private7-day cleaner link. Cleaners can accept,mark done,release a future unlocked house or claim available work. A release gives other cleaners120minutes before the manager sees an unclaimed-house alert; later feasible coverage clears it. No reply-to-email parsing or SMS is enabled. Existing demo roster is retained. A portal welcome email was sent to the samplecontact. The current localhost link opens only on the computer running Turnover.

Hosting is prepared,not deployed. The overlay is infra/hosting/compose.yml with infra/hosting/Caddyfile. Use a dedicated single-owner Linux Docker host and a real DNS name pointing to it,with80/443 reachable. Manager ports3011/3012 stay on127.0.0.1; use SSH forwarding for manager access. Before moving data,stop automation on the oldhost,make a current PostgreSQL backup and preserve its exact paired field encryption key,private routing graph/manifest,Gmail file and read-only PAT. Restore encrypted database data only with the same key. Set file ownership/private permissions for the serveraccount and set LOCAL_UID/LOCAL_GID to that account,not the oldMac IDs. Keep LOCAL_EMAIL_AUTOMATION_ENABLED=false during migration. Set CLEANER_PORTAL_HOST to the chosenDNS name and CLEANER_PORTAL_BASE_URL to its HTTPSorigin in private-data/hospitable.env. Initialize/migrate locally with the existing startup helper,then apply the hosting overlay:

```sh
docker compose --env-file private-data/hospitable.env -f infra/local/hospitable-compose.yml -f infra/hosting/compose.yml config --quiet
docker compose --env-file private-data/hospitable.env -f infra/local/hospitable-compose.yml -f infra/hosting/compose.yml up -d
```

Open the HTTPScleaner page and confirm manager pages are not public before enabling email automation in the private environment and recreating the API with the same merged Composefiles. Keep paired encrypted-data/key backups private and copy them offhost. These are deployment preparation instructions,not evidence of an actual public deployment or a production-readiness claim.

Railway frontend preparation (2026-11-12): frontend/package.json nowprovides npm run build and npm start; startbindsRailway’s PORT(with3000localfallback). A Railway frontend service should use frontend as its rootdirectory,keep HOSPITABLE_READ_ONLY=true and LOCAL_DEMO unset,and use the public HTTPS cleaner origin. This push is not a complete Railway backend migration: current backend intentionally rejects nonlocal database/cache hostnames and protected environments until cloud isolation/authentication are implemented. Its private PostgreSQL data,paired fieldkey,Gmail/PAT files and OSRM graph must be securely migrated/configured; none belong inGitHub. Do not disable guards or enable demo authentication to make public deployment green. Railway service/project configuration and resulting deployment were not verified from the GitHub repository URL alone. Official deployment PORT reference: https://docs.railway.com/deployments/healthchecks .
