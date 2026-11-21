# Airbnb Operations

Three applications for vacation-rental operations, maintained in one repository.

| Application | Purpose | Start here |
| --- | --- | --- |
| Lucena Guest Operations | Manage guest messages, stay requests, cleaning follow-ups, and repair and supply handoffs | [Maintenance connection](guest-recovery/docs/maintenance-connection.md) |
| Cleaner Scheduling | Import reservations, plan turnovers, assign cleaners, and track completion | [Cleaner Scheduling README](cleaner-scheduling/README.md) |
| Maintenance | Review requests, triage repairs, and coordinate work | [Maintenance README](maintenance/README.md) |

Run setup and deployment commands from the corresponding application directory. Use that same directory as the build context for each app's Docker or hosting configuration. The root Dockerfile also supports existing Guest Operations deployments that build from the repository root. Each app keeps its own database, environment, and sign-in. The Maintenance workspace links to the other apps, and Guest Operations can create and check repair tickets through the Maintenance bridge.

## Guest Operations development

From `guest-recovery/`, use Python 3.13 to run `python -m unittest discover -s tests -v`. Start a local instance with `PORT=3200 python app.py` and open `http://127.0.0.1:3200`. For full property-answer retrieval locally, install `requirements.txt` and run `python instruction_search.py --prepare-model` from that directory. The Docker build includes this setup. Configure the server-side environment separately for hosted instances.

Guest Operations can draft property answers from manager-approved guidance and matching past replies. The recovered safety workflow queues manager alerts. Qualifying emergency guidance is sent automatically when Hospitable messaging is configured. Set `AUTOMATIC_SAFETY_REPLIES=0` to pause automatic safety replies.

### Co-located Cleaner Scheduling

Set `INTERNAL_SCHEDULER_WORKSPACE_ID` to the single scheduler workspace ID assigned to this Guest Operations instance. Use the monorepo root Docker build and configure the scheduler's normal `DATABASE_URL` with the restricted `cleaning_app` role and its required backend settings. Apply scheduler migrations through `0029_stay_times` before starting the co-located deployment. Guest Operations calls the scheduler's Python service and never sends a scheduler HTTP request in this mode. The scheduler's HTTP manager and cleaner interfaces retain their existing authentication.

Routine cleaning complaints with an explicit guest-approved entry window and identifiable work enter the scheduler as distinct touch-up tasks. The scheduler’s configured publication and notification settings govern dispatch. Missing consent, hazards, and unclear scopes remain in the inbox for review. Use the regular schedule preview and publication controls to assign them. Guest Operations reads status and completion from the same task. The scheduler approves early check-in only after verified turnover completion. Up to one extra hour is eligible before the checkout-day schedule is published. A longer extension requires an existing published or acknowledged cleaning assignment that still fits, with no other guest affected. Requests without enough evidence stay pending. Confirmed times protect scheduler occupancy and cleaning release times. The original Hospitable booking times stay intact. A fresh, unambiguous rule-confirmed stay request can receive an automatic confirmation through Hospitable. Other replies remain available for review. Existing requests tied to a separate scheduler deployment keep their original connection identity.

## Source history

The imports retain the history available in the source repositories. Cleaner Scheduling came from the GitHub repository created from the original ZIP, so its history starts with that snapshot. Guest Operations includes the issue workflow and Maintenance handoff commits developed locally.

## Use Guest Operations on a phone

Open your hosted Guest Operations HTTPS URL and sign in with your manager account. Tap a message to open its conversation and actions. Tap **Inbox** or use the browser Back button to return to the message list.

To add a shortcut, use **Share → Add to Home Screen** in Safari on iPhone or **Add to Home screen** from the Chrome menu on Android. Guest Operations needs a network connection to retrieve guest messages and submit changes.
