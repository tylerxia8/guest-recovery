# Open questions and decision register

All defaults marked proposed in the [PRD](../PRD.md) remain assumptions until approved in a versioned policy. No business answers were supplied beyond the task brief. Owners below describe roles, not people who have already accepted decisions.

## Development blockers versus launch blockers

There are no external-access blockers to M1–M4 mock development. The implementation agent must choose/pin toolchain versions in M1 and record decisions without inventing credentials. Production adapters and go-live are blocked as described below. A limited authorized fallback is an explicit operating-mode decision, not evidence that full API eligibility was verified.

| ID | Decision / unknown | Proposed path or default | Owner | Blocking scope / evidence needed |
| --- | --- | --- | --- | --- |
| OQ-01 | Hospitable API eligibility and permitted account access | Confirm entitlement with official docs/support | Manager | Full API production launch; account authorization evidence |
| OQ-02 | Verified reservation routes, auth, payloads, status/time semantics and ordering | Complete integration verification register | Implementer | Production adapter; sanitized contract fixtures + official references |
| OQ-03 | Webhooks, signatures, retries, limits and complete snapshots | Disable unsupported capabilities, reconcile | Implementer | Real-time mode; validated callbacks/recovery tests |
| OQ-04 | Authorized calendar/import fallback and acceptable delay | Explicit approved source mode with urgent-booking manual checks | Manager | Any fallback launch; source permissions, field mapping and delay acceptance |
| OQ-05 | Cleaner start-location submission | Secure cleaner-scoped link or manager entry at 17:00 prompt | Manager | Live routing; approved collection flow and per-day fallback policy |
| OQ-06 | Assumed availability and individual working limits | Assume available unless excluded, only within configured shifts/caps | Manager + cleaners | Dispatch; each cleaner's hours/caps/breaks/eligible properties verified |
| OQ-07 | Collection/cutoff and acknowledgment timing | 17:00/17:30; remind +30 min, escalate +60 min after send | Manager | Dispatch activation; policy approval |
| OQ-08 | Missing acknowledgment reassignment | Disabled unless separately approved explicit policy | Manager | Enabling that behavior only; baseline launch can keep disabled |
| OQ-09 | Duration and readiness buffer | Proposed bedroom defaults and 30-minute buffer | Manager | Live schedule; validated measurements per property, 5+ override |
| OQ-10 | Contractor rates, property overrides, repricing and disputes | USD flat per-cleaning, immutable earned snapshots | Manager/accounting | Payment preparation/approval; effective dated rate setup |
| OQ-11 | Payment period and approval process | Manager selects half-open service-date period; no automatic transfers | Manager/accounting | Payment launch; period and correction policy |
| OQ-12 | Vacant-property lead time/no-next-arrival tasks | 48-hour pre-arrival planning horizon; no arrival means checkout plus 48 elapsed hours (PRD 2026-11-07 revision) | Manager | Live planning; approved urgency/target policy |
| OQ-13 | SMS sender registration, opt-in evidence and STOP behavior | Verify appropriate US messaging configuration and current Twilio guidance | Manager + implementer | Production SMS; registered sender, consent records, carrier tests |
| OQ-14 | Production hosting and auth vendor | Managed cloud/PostgreSQL/Redis/OIDC; vendor undecided | Manager + implementer | Deployment; region/budget/backup/auth selection |
| OQ-15 | Routing quotas, traffic model and permitted cache retention | Conservative travel bounds, 30-minute buckets, proposed 60-minute TTL | Implementer + manager | Live routing; verified Google terms/quota and travel validation |
| OQ-16 | End-of-day return travel and breaks | No mandatory return only if approved; configure breaks individually | Manager | Live optimization; route/duty accounting approval |
| OQ-17 | Instruction import fields and secure cleaner access | Per-field local overrides; sensitive details via scoped link | Manager + implementer | Instruction delivery; source permissions and security test |
| OQ-18 | After-start reassignment and partial-work compensation | Manager confirmation before reassign; no automatic partial pay | Manager/accounting | Disruption handling launch; explicit contact/escalation process |
| OQ-19 | Alert recipient/contact channel and operating coverage | Dashboard + independently configured operator alert channel | Manager | Dispatch; tested escalation recipient and coverage hours |
| OQ-20 | SLOs, stale-source threshold, retention and recovery risk | Proposed PRD targets, 30-minute sync freshness, security retention | Manager + implementer | Launch; policy approval, performance/restore evidence |
| OQ-21 | Pilot properties/cleaners and baseline metric collection | Two-week baseline then shadow and two-week pilot | Manager | Live rollout; consented cohort and readiness criteria |

## Decision recording

For each resolution record ID, decision, approver, approval timestamp, rationale, evidence links, affected requirement/test IDs and policy version. Do not erase the original uncertainty; append a resolution. Production launch checklist must identify which mode is being approved and which capabilities remain disabled. Operational defaults may change through versioned policy; changed policy invalidates stale previews. Implementation choices that do not depend on business answers may proceed under mocks.
