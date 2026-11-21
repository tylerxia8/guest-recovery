# Deferred financial API (historical M7)

These routes are not mounted in the active application.

## M7 implemented financial contract additions

Financial endpoints are operational only in the mock development/test application. Payment writes require M/O, `Idempotency-Key`, and `If-Match` for approval/void/corrections; viewers receive 403 even on reads/CSV. Approvals accept a nonblank `note` and expected integer total. Periods are half-open and at most 93 days. Responses include `cleaner_totals:{cleaner_uuid:integer_cents}`, `exclusions:[{task_id,reason}]`, immutable line snapshots, status and version. Batch listing supports UUID cursor and limit 1–100. `no_eligible_work` is 422 with exclusion details, not an empty batch. Invalid snapshot exclusions remain visible through deduplicated manager exceptions.

| Method / route (under `/v1`) | Auth | Request | Response | Validation / expected errors | Idempotency |
| --- | --- | --- | --- | --- | --- |
| POST `/payment-batches/{id}/adjustments` | M/O | `{original_line_id:uuid,amount_cents:signed_integer,adjustment_key:string,reason:string}` + If-Match | 200 revised draft PaymentBatch | Original must be an approved cleaning line; nonzero cents; globally unique key; cannot reverse more than original earning. 409 duplicate key/stale/finalized; 422 invalid correction. Negative cleaner subtotals block later approval. | Key + global correction key |
| POST `/cleaners/{id}/rates/{rate_id}/replace` | O | `{effective_from:date,flat_rate_cents:integer,reason:string}` + If-Match old rule_version | 201 new rate; old range closes at boundary | Boundary inside old range and after every referencing assignment's service date; 409 rate_in_use/stale/overlap; 422 invalid range/blank reason. New rule inherits scope and old upper bound. | Key + serialized rate write |

Rate creation/list now return IDs, scope, effective interval, rule_version, flat_rate_cents, currency and reason. USD cents are strict nonnegative integers (maximum 100,000,000 per rate). Signed adjustment limit is ±100,000,000 cents. `GET .../export.csv` returns `Content-Disposition` and `X-Content-SHA256`; the Next.js proxy forwards these and Idempotency-Key/If-Match without exposing its server-side bearer token. Repeated approval with the original key/body returns the original response; different keys against finalized state return 409. Void/reprepare moves lines and preserves the old frozen view; corrections move only on exact-period re-preparation.


Historical route table entries:

| POST `/cleaners/{id}/rates` | O | `{property_id:uuid\|null,effective_from:date,effective_to:date\|null,flat_rate_cents:int,currency:"USD",reason:string}` | 201 `{id,rule_version,...request}` | 409 overlapping effective range; 422 negative cents; immutable old snapshots | Key |
| GET `/cleaners/{id}/rates` | O/M | effective_on:date? | 200 page rate versions | authorization for money | Read |
| POST `/payment-batches/prepare` | M | `{period_start:date,period_end:date,currency:"USD"}` | 201 PaymentBatch | half-open dates, end>start; 409 concurrent earned-key conflict, 422 no eligible lines/missing rate | Key + global earned key |
| GET `/payment-batches` | M | status?, cursor? | 200 page PaymentBatch summary | valid status | Read |
| GET `/payment-batches/{id}` | M | UUID | 200 PaymentBatch | 404 missing | Read |
| POST `/payment-batches/{id}/approve` | M | `{expected_total_cents:int,note:string}` + If-Match | 200 PaymentBatch | 409 changed total/state; approval freezes lines | Key + version |
| POST `/payment-batches/{id}/void` | M | `{reason:string}` + If-Match | 200 PaymentBatch | only draft; approved corrections require adjustment workflow; 409 approved | Key + version |
| GET `/payment-batches/{id}/export.csv` | M | UUID | 200 text/csv, Content-Disposition attachment | approved only, otherwise 409; audits each retrieval; formula-safe fields | Repeatable frozen export |
