# Session handoff

Updated 2026-10-30. User confirms request/output works, but report UI is hard to understand. Startup, output guidance and Git commands now lead README. DB migrations pass; detailed runtime/provider/UI acceptance remains open.

## Scope
- Latest scope: record working request and poor output usability; document end-to-end startup/output and Git commands; update .gitignore. Docs/config only this step. Earlier live test remains authorized; account/quiet usage confirmed. No new accounts/purchases/agents. Ponytail.
- Preserve plan-proposal.md, TermsLens_Project_Plan.md and Assignment_10_Report.pdf. Start with AGENTS/this handoff. MySQL/full stack required; README/workflow/checklist hold contracts.

## Existing foundation
- Paste/URL/discovery, owned jobs/sessions/CSRF/rates, immutable captures/reports, worker leases/recovery and atomic publication. Eight rule groups/profiles/Unicode; library empty-v1. AI consent/quotas/provenance persist; unknown outcomes never auto-resend.
- Groq model openai/gpt-oss-20b/key private. Limits 30 RPM/1000 RPD/8000 TPM/200000 TPD and dated account/quiet-usage confirmation in var/groq-demo-evidence.json. User reports working output after setup instructions; current saved mode/model/usage not inspected. Do not infer provider acceptance or repeat setup blindly.
- URL children/test storage isolated, test Groq cleared; captures recover without refetch. versions.md/url-fetching.md cover immutable revisions, A -> B -> A, context reruns and compatible reuse.

## Latest implementation
- comparisons-deletion.md holds contracts/eight prepared MySQL checks. Owner-before-document locks fix reviewed FK-order risk; tombstones/cursor cleanup preserve other reports/quotas. OS barriers fence stale writes. Runtime proof pending.
- Both schemas use 20261029_lifecycle. Worker logs startup after singleton lock acquisition, catches Ctrl+C and disposes engine; interrupted jobs are not retried inline.
- README starts with both terminal commands, submit/read/reopen guidance, scores vs severity, unrated filters, baseline/profiles/comparison/deletion and Git updates. Fresh setup moved intact into RUNNING.md. No UI redesign yet; prioritize readable summary/concerns/evidence and simpler controls next.
- .gitignore now also covers tool caches, coverage/browser reports, venv and TypeScript build metadata. Secrets/var excluded; source fixtures/examples/lockfiles retained. Branch main and remote origin observed; earlier commit fa4cf02, push unverified. No staging/commit/push this step; Git commands prepared for user.
- DEMO.md covers clipboard and guarded AI startup. eval/fixtures/wordpress-terms-demo.md: 52-word WordPress refunds excerpt, attributed CC BY-SA 4.0, source paragraph unchanged. Existing online capture; not freshly fetched or human-labeled.
- scripts/configure_groq_demo.py verifies official framing/demo tokens and GET /models, then writes private readiness (<=4h within confirmation's 24h) and enables AI. No inference, silent renewal or ledger reset. Use user's working terminal when setup is needed. app.manage retains safe errors.

## Actual checks
- Retain worker/test-Groq isolation (5) and fake-setup (5) passes. This step: frozen/doc limits, git diff --check and ignore rules pass; no tracked file matches ignores. No runtime/provider retest.
- User: both migration checks PASS MySQL 8.0.28, termslens_db/termslens_test, utf8mb4, revision 20261029_lifecycle (G1-04/05 closed). Registration and basic request/output work. User finds report UI poor/hard to understand. Precise findings/provenance, reopen/isolation/recovery and UI accessibility remain unobserved.
- Earlier agent DB check hit MySQL 2003; GET /models failed ConnectError. User's later working flow supersedes their blocker, not agent restrictions. No new provider call or artifact inspection this step.
- Retain: six basic backend checks, frontend build/18 Node tests, 66 owner-lock/harness checks, eight-test opt-in guard, prior migrations/181-test baseline and URL/version/lifecycle evidence. MySQL/browser runtime remains pending.

## Deferred facts
- MySQL 1045 resolved; existing scoped accounts and utf8mb4_bin schemas work. Do not recreate/reset accounts or request passwords. Agent restrictions are separate.
- Private URLs/key preserved; dotenv last definition wins, process environment overrides. .venv Python 3.11.3 requirements/pip check passed (Alembic 1.13.1/PyMySQL 1.1.1/httpx 0.27.0). No extra .env pasting needed.
- Agent HTTP timed out at 45s; socket 10013/browser restrictions persist. User WordPress HTTPS/Groq TCP passed. Retry only after changed conditions. Fresh npm ci, real MySQL/HTTP/browser acceptance pending.
- WordPress: var/eval/sources/wordpress-terms/8486343d2107439f860e17420a50dfd1; hashes verified, October 6, 2026/CC BY-SA 4.0 observed. Rights review pending. var/eval/reviews/wordpress-review-v1.json has 417 candidates, zero labels. See eval/README.md.
- Preserve var archives/caches: prior cleanup auto-rejected, sandbox approvals disabled. No working.md or duplicate handoff.

## Pending work
- Next proposed task: improve output UI summary, issue prioritization, score/unknown explanations and evidence navigation; preserve scoring/provenance. User requested recording the issue, so this turn delivered documentation rather than redesign. Full gates remain pending.
- Normal demo startup is at README top. Reopen existing reports to inspect output; no repeat AI calls needed for UI work. Only configure Groq when missing/expired readiness needs attention; keep account identity/ledger stable. No --test for AI or auto-resubmission after unknown outcomes.

## Reproduction
- Root: .venv/Scripts/python.exe scripts/check_context.py; git diff --check; git ls-files -ci --exclude-standard. README has startup/copy/Git commands; RUNNING.md fresh setup; DEMO.md guarded AI setup. Existing report controls match ReportView.tsx, DocumentHistory.tsx and ComparisonPanel.tsx.
