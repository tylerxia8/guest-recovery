# SMS workflows

House-handoff requirement (2026-11-10), planned and not enabled: cleaners must be able to punt one explicitly identified current house, without marking their entire day unavailable. Other eligible cleaners explicitly claim that specific offer; a fresh feasible claim changes ownership atomically and produces current-version notices. A stale or losing claim must leave ownership unchanged. If unclaimed after the manager-approved 120 minutes, alert the property manager that the house has no cleaner. A punt does not permanently exclude that property for the cleaner. Preserve hard constraints and manager locks/possibly-started/completed protections. Do not advertise a PUNT/TAKE command in HELP or templates until its durable state, freshness, feasibility, notification and timer implementation is complete. In particular, a superseded schedule must not tell a cleaner they have no houses if their replacement version retains other work.

Twilio is proposed, behind `SmsGateway`. Mock gateway records synthetic intents and never sends externally. Production requires consent, registered sender, approved policies and both integration/dispatch flags. Validate current Twilio behavior using official documentation/SDK in M5; provider field/signature details are not specified as verified here.

## Consent and addressing

Bind inbound sender using normalized E.164 to a unique active cleaner identity after signature validation. Never rely on body-provided identity. Unrecognized numbers receive only an approved generic HELP response when permitted; no operational data. STOP processing is allowed even when the cleaner is unavailable or no schedule exists. Consent pending/opted-out blocks all application operational SMS; approved opt-out confirmation behavior must align with Twilio's own handling to avoid duplicate prohibited replies. Service alerts remain visible to managers through independent dashboard/operator channels.

## Message templates (synthetic identifiers)

Do not include real addresses, entry codes or full instructions in SMS. Property aliases and authenticated short-lived detail links are proposed; establish a secure cleaner-scoped link flow before shipping links. Links reveal only current assigned work, expire (proposed 24 hours), can be revoked, are hashed at rest, rate-limited, excluded from logs and never grant manager access.

| Purpose | Template |
| --- | --- |
| Availability at proposed 17:00 | `Tomorrow {date}: availability is assumed within your configured hours. Reply UNAVAILABLE if you cannot work. To update start location, use your secure link or contact manager before 17:30 Eastern. HELP for help; STOP to opt out.` |
| Assignment at 18:00 | `{date} schedule V{version}: {task_id} {property_alias} {start}-{end} Eastern; ... Reply CONFIRM {date} V{version} to accept all current jobs. DONE {task_id} when complete. UNAVAILABLE if unable. HELP or STOP.` |
| Reminder +30 min | `Acceptance still needed for {date} V{version}. Reply CONFIRM {date} V{version}, or contact manager. Delivery is not acceptance.` |
| Revised schedule | `Schedule changed: {date} V{new_version} replaces V{old_version}. Removed: {ids}. Current jobs: {summary}. Reply CONFIRM {date} V{new_version}.` |
| Cancellation / empty route | `{date} V{version}: prior jobs {ids} removed; no jobs currently assigned. Do not act on the prior version. Contact manager if work already started.` |
| Completion receipt | `Recorded DONE {task_id} for your current assignment. Thank you.` |
| Unavailable receipt | `Marked unavailable for {date}. We are reviewing affected work. Contact manager if a job has started.` |
| HELP | `Commands: CONFIRM {date} V{version}; DONE {task_id}; UNAVAILABLE [YYYY-MM-DD]; HELP; STOP. For urgent issues contact your configured manager.` |
| Ambiguous/stale | `No change made: {safe_reason}. Use {current_command} or contact manager.` |

Long schedules are grouped/chunked with explicit date/version and part numbering. One logical schedule intent may have multiple parts; maintain part intent keys and delivery state. Acknowledgment applies to the logical version, not an SMS part. Avoid secrets in URL query strings; token-bearing links require the controls above. Proposed message length/segment budgets require cost review and carrier testing.

## Parsing and state transitions

Trim surrounding whitespace, collapse internal whitespace, uppercase command tokens (IDs are case-insensitive aliases mapped to UUID), reject unrecognized extra arguments. Accept exact `CONFIRM`, `CONFIRM YYYY-MM-DD V<number>`, `DONE <TASK_ID>`, `UNAVAILABLE`, `UNAVAILABLE YYYY-MM-DD`, `HELP`, `STOP`. Provider-managed reserved opt-out keywords must also be honored when officially supported. Do not parse free prose or infer acceptance from `OK`.

- CONFIRM with explicit date/version must match sender's current published version. Bare CONFIRM is valid only when exactly one current unacknowledged version was sent to this cleaner across relevant dates; otherwise ask for date/version. Store acknowledgment unique per cleaner schedule version. Duplicate confirmation returns same result; old version never acknowledges the new route.
- DONE requires exactly one task ID and sender's active current assignment. A short ID must be globally unique among operational tasks (collision causes longer alias); never resolve by fuzzy matching. Reject wrong cleaner, superseded assignment, canceled task or impossible future completion. Record receipt time as actual completion instant. A duplicate DONE is a no-op with the same receipt. If unacknowledged, allow completion with `COMPLETED_WITHOUT_ACK` exception; do not fabricate prior acceptance. If work has been reassigned, quarantine stale completion for manager review.
- Bare UNAVAILABLE applies only to the single service date identified by the latest actionable schedule/availability prompt; if multiple dates are possible, ask for a date without changing availability. Explicit date is preferred. Mark dated availability unavailable, supersede/replan unstarted unlocked assignments, preserve locks and possibly-started work as exceptions. Explicit unavailability authorizes replan, not physical infeasibility.
- HELP returns commands/contact. STOP immediately records opted-out status, cancels queued application messages, creates an operational-contact exception, and prevents subsequent application operational sends. STOP does not automatically cancel physical cleaning obligations or erase consent evidence/history. Resumption requires verified fresh consent, not an arbitrary CONFIRM or manager phone edit.

## Timers, delivery and deduplication

Default dispatch 18:00 Eastern previous day; reminder +30 minutes after actual send acceptance; escalation +60 minutes. These are configurable proposed acknowledgment defaults requiring approval. Reminder and escalation jobs recheck current version, opt-out, acknowledgment and job freshness. New versions invalidate old timers and start their own acknowledgment interval. Missing acknowledgment never triggers automatic reassign under default policy.

Outbox key: `sms:{purpose}:{cleaner_id}:{service_date}:{version}:{part}`. Reminder/escalation uses a distinct purpose once per version. Before send, lock intent and recheck current version/consent/feature flags. Provider accepted status means submitted, not delivered or acknowledged. Normalize delivery to queued/accepted/sent/delivered/failed/undelivered/unknown; keep acceptance separate. Repeated or out-of-order callbacks cannot downgrade delivered to sent; contradictory terminal callbacks create review evidence.

Retry only definite pre-acceptance transient failures, proposed maximum three attempts with bounded backoff. If a send times out after provider may have accepted it, mark `unknown`, reconcile using documented provider facilities and/or escalate to manager; do not blindly resend and claim exactly-once delivery. A durable intent prevents duplicate worker sends but cannot guarantee carrier exactly-once behavior. Permanent errors/undeliverable messages create delivery exceptions immediately. Acknowledgment reminder is not a retry of assignment delivery. STOP and schedule supersession invalidate queued retries.

## Escalation

At +60 minutes create a deduplicated missing-acknowledgment exception and alert manager dashboard/operator channel with cleaner/date/version, delivery status and affected deadline tasks. Failed delivery raises a separate exception immediately. Manager may contact cleaner, record evidenced acceptance, reassign after explicit unavailability, or choose a documented approved missing-ack policy later. Every action is audited. If SMS/provider is down, retain outbox with expiry, surface failure and do not send stale instructions during recovery; recompute current versions first.

## Eight-person demo team and provider setup (2026-11-11)

The manager confirmed every cleaner persona is fictional and requested eight total, four part-time. Reserved NANPA555-0100..0199 contacts are non-working demonstration numbers. They cannot receive SMS and are not evidence of consent. Existing persona hours remain; new full-time examples use weekday08:30–17:00 with12:30–13:00 break, and part-time examples use weekday08:30–12:30. All new examples share the existing saved demo start and may be edited through onboarding. Local SMS remains recording-only; no provider account, real sender, consent or dispatch activation is fabricated.

Prepared assignment text now requires the verified source formatted destination/unit for Hospitable properties, in addition to house alias, planned Eastern time, deadline, task token and CONFIRM/DONE commands. Destination text is encrypted in message storage and not logged. Superseded-version cancellation no longer falsely claims the cleaner has no current jobs; it directs them to the latest schedule. No send path or isolation guard is weakened by these wording/data preparations.

Provider recommendation: Twilio, subject to real account entitlement and sender verification. Official [current trial documentation](https://www.twilio.com/docs/usage/trials), verified2026-11-11, offers a30-day no-card trial with verified-recipient and predefined-content restrictions; it cannot send this app’s custom assignment bodies to fake contacts. Official [test-credential documentation](https://www.twilio.com/docs/iam/test-credentials) now states new console users cannot create legacy test credentials; do not instruct new users to obtain them. Existing legacy test credentials have simulated responses and do not deliver messages/callbacks. Real custom SMS requires the appropriate upgrade, registration, real consenting recipients and verified production integration. The existing Twilio-named webhook routes still implement only mock signatures and are not a live integration.

Email interim channel (2026-11-12): owner explicitly enabled automatic cleaner schedule emails through the locally configured Gmail account while SMS registration remains pending. This separate channel neither grants SMS consent nor enables carrier dispatch. It uses durable encrypted outbox records,semantic deduplication,limited connection retries and manual attention for uncertain submissions. Email replies are not parsed into acceptance/completion,punt or claim actions.

Portal response channel (2026-11-12): schedule emails include private portal links for Accept,Done,release and claim. Email delivery is not acceptance. Gmail inbox replies are not parsed. Released houses notify eligible saved-email contacts through the same durable encrypted queue. No claimant within120minutes triggers CLEANING_UNCLAIMED on the manager dashboard; a later feasible claim resolves it. The original and all prior releasing cleaners cannot reclaim the same task. SMS remains disabled and Hospitable remains GET-only.
