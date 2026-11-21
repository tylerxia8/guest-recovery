# Cleaner compensation and payment preparation

**Current scope (2026-11-09):** automate cleaner scheduling only. Payments, compensation setup and the standalone Settings screen are deferred. Their historical contracts and database records remain for possible restoration; they are not active features. Scheduling policies, hours, consent, eligibility, travel and deadlines remain because they determine feasible schedules.

Implements FR-21/FR-22. Release 1 prepares contractor liabilities; it never invokes a bank-transfer API or stores banking credentials.

## Rate rules

Use USD integer cents and fixed per-cleaning rates in initial release. Cleaner+property override takes precedence over cleaner default, selecting effective half-open date range `[effective_from,effective_to)` by assignment business-local service date. Reject overlapping effective versions at the same scope. Hourly, bedroom multipliers, bonuses, deductions, taxes and partial-work policies require later explicit rules; do not infer them from property size. No applicable rate is a blocking compensation exception, not a zero-dollar payment. Explicit approved zero-cent rate is distinguishable from missing.

On publication snapshot rate ID, rule version, scope, amount_cents, currency, effective date, cleaner/property/task and calculation schema version. Acknowledgment includes this compensation version. Updating a rate cannot rewrite existing published or completed compensation. Before completion, intentional repricing requires a new version, reason, manager action and fresh acknowledgment. Completed work retains original snapshot; any correction becomes an approved signed adjustment with reason and original-line reference.

Synthetic example: cleaner C-A default 7,500 cents, property P-B override 9,000 cents. Two completed jobs at P-A and P-B yield 16,500 cents. A later default change to 8,000 cents does not alter those lines. Display `$165.00`, store `16500`; use decimal formatting, never binary floating point for money.

## Eligibility and preparation

Eligible work is completed and non-reversed, has valid frozen compensation, is not under unresolved completion/payment dispute, and is not already reserved or paid in another batch. Select by service date within the manager-chosen half-open period, not SMS receipt date. Payment periods are unresolved; no automatic weekly assumption. Late completions are included in a subsequent preparation for their original service dates, with a clear late-added indicator.

Transactionally reserve eligible task-based earned-work keys and insert lines with snapshots; overlapping concurrent preparation cannot create duplicate liability. Superseding assignments do not generate new earned-work keys for the same cleaning. Batch total equals sum of lines in one currency. If some work has missing rates, show excluded work and exceptions; never silently zero it out. An empty eligible set returns a validation response rather than a misleading approved batch.

## Lifecycle and approval

`draft → approved` or `draft → void`. Approval requires manager role, current batch version and expected total. Show per-cleaner totals, task/property aliases, service dates, rate provenance and unresolved exclusions before approval. Approved contents are immutable; repeat approval returns same batch, never a second liability. Approved does not mean transferred or paid. If manual settlement tracking is later requested, design a separate audited external-payment-reference workflow, not a transfer integration.

Void a draft with a reason and atomically release/move its reserved lines for re-preparation while retaining history and a unique active payable record. Prefer moving existing unique earned-work lines into a replacement draft and recording history, never copying them. Approved corrections require distinct adjustment keys and signed amounts linked to original lines; block negative total payouts pending explicit manager policy. Do not delete approved work to free uniqueness constraints.

## CSV export

Approved batches only, UTF-8, deterministic line ordering `(cleaner_id,service_date,task_id,line_id)`. Header: `batch_id,line_id,cleaner_id,cleaner_name,task_id,property_alias,service_date,rate_version,amount_cents,currency`. No phones, bank details, access codes or real addresses. Quote fields according to CSV rules and neutralize spreadsheet formulas in text cells beginning with `=`, `+`, `-`, `@`, tab or carriage return (prefix a safe apostrophe). Numeric amount_cents remains numeric, including authorized adjustment negatives. Include a file digest in export metadata/audit; re-export yields same financial content and never creates new lines or implies transfer.

## Controls and reconciliation

Audit rate creation, repricing, preparation, exclusions, approval, void, adjustment and export. Dashboard exposes eligible unbatched work, draft/approved totals and exceptions. Reconcile each task against at most one cleaning liability; sum of cleaner subtotals must equal batch total. Manager reviews disputes before approval. [Acceptance tests](ACCEPTANCE_TESTS.md) cover snapshot stability, precedence, repeat preparation/approval/export, concurrent batches and completion deduplication.

## M7 implementation boundary

All operations currently run only with mock application startup and synthetic data. Existing minimal M4 snapshots are enriched from their original referenced rate version, never the current rate. An inconsistent/missing snapshot creates `MISSING_COMPENSATION_SNAPSHOT`; a disputed or changed completion creates `PAYMENT_REVIEW_REQUIRED`. No eligible work returns 422 with exclusions and preserves deduplicated exceptions without creating an empty batch. An explicitly zero-cent rate can still be approved.

Draft/approved/void responses include per-cleaner totals and exclusions. Finalized batches store frozen line JSON, so later moving a voided line into a replacement does not erase the old batch's contents. Correction keys are global; a void does not release them. Re-preparing a voided correction requires the same half-open batch period; the same line moves, with its original service date retained. Negative subtotals for any cleaner block approval, even if the overall total is positive. An adjustment may not reverse more than the original earning; larger/partial-work disputes need manager/accounting resolution rather than an invented rule.

Owner rate replacement can split an existing effective period only after all assignments referencing the old rate; published compensation is not repriced. It closes only `effective_to` and appends a new rate version in one audited transaction. Database constraints reject overlapping default/property periods, and a trigger prohibits changing the original amount/scope or deleting rate history. Migration preflight must review existing overlaps before upgrading an existing operational database; never silently repair financial records.
