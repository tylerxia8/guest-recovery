# Airbnb Operations

Three applications for vacation-rental operations, maintained in one repository.

| Application | Purpose | Start here |
| --- | --- | --- |
| Guest Recovery | Review guest issues, draft replies, prepare supply purchases, and send repair handoffs | [Maintenance connection](guest-recovery/docs/maintenance-connection.md) |
| Cleaner Scheduling | Import reservations, plan turnovers, assign cleaners, and track completion | [Cleaner Scheduling README](cleaner-scheduling/README.md) |
| Maintenance | Review requests, triage repairs, and coordinate work | [Maintenance README](maintenance/README.md) |

Run setup and deployment commands from the corresponding application directory. Use that same directory as the build context for each app's Docker or hosting configuration. The root Dockerfile also supports existing Guest Recovery deployments that build from the repository root. Each app keeps its own database, environment, and sign-in. The Maintenance workspace links to the other apps, and Guest Recovery can create and check repair tickets through the Maintenance bridge.

## Guest Recovery development

From `guest-recovery/`, use Python 3.13 to run `python -m unittest discover -s tests -v`. Start a local instance with `PORT=3200 python app.py` and open `http://127.0.0.1:3200`. For full property-answer retrieval locally, install `requirements.txt` and run `python instruction_search.py --prepare-model` from that directory. The Docker build includes this setup. Configure the server-side environment separately for hosted instances.

Guest Recovery can draft property answers from manager-approved guidance and matching past replies. The recovered safety workflow queues manager alerts. Automatic emergency replies to guests require `AUTOMATIC_SAFETY_REPLIES=1`.

### Co-located Cleaner Scheduling

Set `INTERNAL_SCHEDULER_WORKSPACE_ID` to the single scheduler workspace ID assigned to this Guest Recovery instance. Use the monorepo root Docker build and configure the scheduler's normal `DATABASE_URL` with the restricted `cleaning_app` role and its required backend settings. Apply scheduler migration `0028_guest_touchups` before starting the co-located deployment. Guest Recovery calls the scheduler's Python service and never sends a scheduler HTTP request in this mode. The scheduler's HTTP manager and cleaner interfaces retain their existing authentication.

Manager-confirmed return visits enter the scheduler as distinct touch-up tasks, with the guest-approved entry window. Use the regular schedule preview and publication controls to assign them. Guest Recovery reads status and completion from the same task. Stay-change requests validate the booking in the scheduler and stay pending for manager review. This mode does not automatically change booking times. Existing requests tied to a separate scheduler deployment keep their original connection identity.

## Source history

The imports retain the history available in the source repositories. Cleaner Scheduling came from the GitHub repository created from the original ZIP, so its history starts with that snapshot. Guest Recovery includes the issue workflow and Maintenance handoff commits developed locally.
