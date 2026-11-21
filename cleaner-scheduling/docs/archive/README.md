# Deferred finance implementation

The manager narrowed the project to cleaner scheduling on 2026-11-09. Payment/rate routes, calculation services and finance acceptance tests were removed from the runtime and active suites. These text archives preserve the M7 implementation for a future reviewed restoration. Removed frontend code remains in Git history.

This is not a supported optional module or a feature flag. Restoring it requires a new product decision, schema review (assignments now allow missing rates/amounts), repaired imports and full acceptance tests. Historical tables, immutable snapshots and migrations remain intact. Do not infer compensation or payment eligibility from new scheduling-only completions.

The active app keeps policy approval, availability, consent, travel, source health, audit and exception handling because they protect scheduling. This repairs the previous financial contracts to follow the revised PRD. Historical milestone evidence describes the app at that time, not current feature availability.
