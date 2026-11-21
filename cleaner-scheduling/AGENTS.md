# Engineering instructions for the implementation agent

- PRD.md is the authoritative product specification. Documents in `/docs` define implementation contracts; if they conflict, follow PRD.md and repair the conflicting contract with a recorded rationale.
- This initial handoff is documentation only. Subsequent implementation must follow the eight milestones in docs/IMPLEMENTATION_PLAN.md, one at a time, starting with mock adapters and synthetic reservation/cleaner data.
- Do not invent third-party endpoints, authentication, webhook signatures, payloads, or guarantees. Verify official documentation and account entitlements before implementing a production adapter; record evidence in docs/HOSPITABLE_INTEGRATION.md.
- Never commit credentials, real property addresses, property access codes, reservation guest data, or cleaner personal data. Keep samples synthetic and redact logs.
- Write automated tests for every milestone (Pytest for backend/domain; Playwright for manager workflows). Report executed commands, results, skipped checks, and unresolved blockers accurately.
- Preserve migration history. Use additive migrations and reviewed data transitions; never edit a migration already applied to a shared environment.
- Never publish infeasible schedules. Validate the final plan independently of the solver, transactionally check freshness, and create exceptions for uncovered work.
- Never overwrite manager-locked assignments. Conflicting locks require manager resolution; overrides cannot bypass hard feasibility constraints.
- Never send production SMS during development. Use a recording mock gateway by default. Keep production dispatch behind a feature flag and environment isolation; enabling integrations alone must not enable dispatch.
- Never initiate bank transfers or add payment-transfer capabilities to the initial release. Only prepare, approve, and export payment batches.
- Treat SMS delivery and explicit cleaner acceptance as separate facts. Missing acknowledgment alone does not authorize automatic reassignment.
- Use America/New_York for business dates and schedules; store timestamps in UTC. Never use server-local time for dispatch.
- Protect concurrent schedule publication, completion, imports, and payment preparation with transactions, version checks, and idempotency keys.
- Update the milestone progress table, relevant contracts, and acceptance tests when completing work. Do not mark a milestone done without its definition of done and evidence.
