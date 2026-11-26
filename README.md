# Airbnb Operations

Three applications for vacation-rental operations, maintained in one repository.

| Application | Purpose | Start here |
| --- | --- | --- |
| Guest Messages | Manage guest messages, stay requests, cleaning follow-ups, and repair and supply handoffs | [Maintenance connection](guest-recovery/docs/maintenance-connection.md) |
| Cleaner | Import reservations, plan turnovers, assign cleaners, and track completion | [Cleaner README](cleaner-scheduling/README.md) |
| Maintenance | Review requests, triage repairs, and coordinate work | [Maintenance README](maintenance/README.md) |

Run setup and deployment commands from the corresponding application directory. Use that same directory as the build context for each app's Docker or hosting configuration. The root Dockerfile also supports existing Guest Messages deployments that build from the repository root. Each app keeps its own database and environment. When shared sign-in is enabled, Cleaner issues short-lived grants bound to the manager's current workspace. Guest Messages and Maintenance use those grants to open the manager's workspace and hand off work without a second login.

## Guest Messages development

From `guest-recovery/`, use Python 3.13 to run `python -m unittest discover -s tests -v`. Start a local instance with `PORT=3200 python app.py` and open `http://127.0.0.1:3200`. For full property-answer retrieval locally, install `requirements.txt` and run `python instruction_search.py --prepare-model` from that directory. The Docker build includes this setup. Configure the server-side environment separately for hosted instances.

Guest Messages can draft property answers from manager-approved guidance and matching past replies. The recovered safety workflow queues manager alerts. Qualifying emergency guidance is sent automatically when Hospitable messaging is configured. Set `AUTOMATIC_SAFETY_REPLIES=0` to pause automatic safety replies.

### Shared manager workspace

Enable the operations login and callback origins on the Cleaner frontend, and
enable the corresponding handoff features on the Cleaner API. In Guest Messages,
configure `OPERATIONS_WORKSPACE_DIRECTORY` on the private volume,
`OPERATIONS_TURNOVER_ORIGIN` as the Cleaner web HTTPS origin, and
`OPERATIONS_MAINTENANCE_ORIGIN` as the Maintenance bridge HTTPS origin. The
Maintenance API verifies the same Cleaner issuer through its
`OPERATIONS_TURNOVER_ORIGIN` setting and requires shared workspace provisioning
and repair handoff to be enabled. Build the Maintenance web image with a
non-demo `NEXT_PUBLIC_DEMO_FIXTURE` and its private API origin configured.
The production API accepts verified Cleaner membership while keeping local
password login and the demo session disabled.

Managers connect Hospitable in Cleaner. The shared inbox reads stay messages
through that connection and saves references to cleaning and repair requests
in the matching Guest Messages workspace. Reopen each request from its guest
case to see the current status. Cleaning requests show the assigned cleaner,
planned window, schedule email status, and cleaner response. Cleaner controls
publication and notifications. Maintenance controls repair intake and assignment.
Managers can also submit early check-in and late checkout requests from a
verified stay. Cleaner applies the stay-change policy and keeps the imported
reservation times. The manager can then communicate the outcome to the guest.

The shared inbox saves guest reply drafts. A private workspace can also submit
an explicitly approved draft through its existing Hospitable connection when
the reply pilot is configured and the connection has verified write access.
Review the current conversation and the saved text before approving. The inbox
shows whether Hospitable accepted the request, rejected it, or returned an
uncertain outcome. Check an uncertain outcome in Hospitable.

Opening a shared case checks the recent guest messages for cleaning, repair,
stay-change, supply, and safety issues. Active supply requests have catalog
suggestions. Pending requests appear in the manager action inbox after a
manager opens the guest case. Approving the proposal records the decision.
The manager checks stock, price, and arrival and places the order in Amazon.
Safety reports show a suggested response for the manager to review. Managers
can save reusable parking, trash, and checkout instructions for the current
property. Matching answers appear in the reply draft for review. Access codes
and passwords require a stay-specific source.

The standalone Guest Messages installation still owns the background
automation, outbound safety replies, and email order notices. Keep access to
its historical case records during migration. Coordinate the switch so two
installations cannot send replies for the same reservation.

### Co-located Cleaner

Set `INTERNAL_SCHEDULER_WORKSPACE_ID` to the single scheduler workspace ID assigned to this Guest Messages instance. Use the monorepo root Docker build and configure the scheduler's normal `DATABASE_URL` with the restricted `cleaning_app` role and its required backend settings. Apply scheduler migrations through `0034_legacy_return_lookup` before starting the co-located deployment. Guest Messages calls the scheduler's Python service and never sends a scheduler HTTP request in this mode. The scheduler's HTTP manager and cleaner interfaces retain their existing authentication.

Routine cleaning complaints with an explicit guest-approved entry window and identifiable work enter the scheduler as distinct touch-up tasks. The scheduler’s configured publication and notification settings govern dispatch. Missing consent, hazards, and unclear scopes remain in the inbox for review. Use the regular schedule preview and publication controls to assign them. Guest Messages reads status and completion from the same task. The scheduler approves early check-in only after verified turnover completion. Up to one extra hour is eligible before the checkout-day schedule is published. A longer extension requires an existing published or acknowledged cleaning assignment that still fits, with no other guest affected. Requests without enough evidence stay pending. Confirmed times protect scheduler occupancy and cleaning release times. The original Hospitable booking times stay intact. A fresh, unambiguous rule-confirmed stay request can receive an automatic confirmation through Hospitable. Other replies remain available for review. Saved requests change connection only when the previous HTTPS origin and workspace match the cutover settings. Pending proposals, approvals, and notification retries from the previous connection require review in Cleaner.

## Source history

The imports retain the history available in the source repositories. Cleaner came from the GitHub repository created from the original ZIP, so its history starts with that snapshot. Guest Messages includes the issue workflow and Maintenance handoff commits developed locally.

## Use Guest Messages on a phone

Open your hosted Guest Messages HTTPS URL and sign in with your manager account. Tap a message to open its conversation and actions. Tap **Inbox** or use the browser Back button to return to the message list.

To add a shortcut, use **Share → Add to Home Screen** in Safari on iPhone or **Add to Home screen** from the Chrome menu on Android. Guest Messages needs a network connection to retrieve guest messages and submit changes.
