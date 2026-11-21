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

Guest Recovery can draft property answers from manager-approved guidance and matching past replies. The recovered safety workflow queues manager alerts. Automatic emergency replies to guests require `AUTOMATIC_SAFETY_REPLIES=1`. The recovered cleaner-return and stay-adjustment flows need a scheduler deployment that supports their API endpoints. The Cleaner Scheduling snapshot in this repository does not expose those endpoints.

## Source history

The imports retain the history available in the source repositories. Cleaner Scheduling came from the GitHub repository created from the original ZIP, so its history starts with that snapshot. Guest Recovery includes the issue workflow and Maintenance handoff commits developed locally.
