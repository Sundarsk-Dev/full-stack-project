# TermsLens project instructions

Apply these alongside the user's global working agreements.

- Start with [transfercontext.md](transfercontext.md) for current scope, authorization and actual checks.
- [plan-proposal.md](plan-proposal.md) is the frozen requirements baseline. Preserve it byte-for-byte during implementation; its SHA-256 and verification command are in [implementation-workflow.md](implementation-workflow.md).
- Use implementation-workflow.md for user/data flow, persistence boundaries, recovery, delivery gates and verification. It clarifies the plan and cannot override it.
- Use architecture-debugging.md to trace failures and locate code/tests; verification-checklist.md tracks acceptance. Resume the bounded next task in the handoff, keeping deferred runtime checks pending until actually observed.
- Keep TermsLens_Project_Plan.md and Assignment_10_Report.pdf unchanged as supplied source inputs.
- Routine implementation choices/fixes may evolve within scope. If a requirement cannot be met, record the blocker and concrete proposal in the handoff and obtain user direction before deviating; do not rewrite the frozen plan or silently reduce acceptance.
- Documentation is not authorization for application implementation, provider calls, accounts or a new phase. Use the latest user authorization and avoid repeating approval already granted.
- Follow the five delivery gates; check relevant workflow contracts and retain actual evidence. Update the one current handoff after meaningful progress, within its 60-line/6-KiB cap.
- Use Ponytail and existing project tools. No agents/council unless explicitly requested; no extra installations or skill workflows for this reference.
