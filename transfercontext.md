# Session handoff

Updated 2026-10-29. Baseline/basic checks and Windows run guide ready. Git initialized, but remote setup/push blocked by host permissions. Runtime acceptance and Gate 5 remain open.

## Scope
- Latest scope: finish quickly, basic tests, run instructions and push baseline to https://github.com/Sundarsk-Dev/full-stack-project.git. Gitignore first. No repeated Groq testing; prior bounded live-test authorization still needs readiness. Ponytail; no agents/new accounts/purchases.
- Preserve plan-proposal.md, TermsLens_Project_Plan.md and Assignment_10_Report.pdf. Start with AGENTS/this handoff. MySQL/full stack required; README/workflow/checklist hold contracts.

## Existing foundation
- Paste/URL/discovery, owned idempotent jobs, sessions/CSRF/rates, immutable captures/reports, worker lock/leases/heartbeat/three attempts, stale fencing and atomic publication.
- Eight rule groups/unknowns/caps/Unicode; gate2-rules-v1/library empty-v1. Shared scoring and stored profiles/baseline views make no calls/writes. AI consent/quotas/responses/provenance persist; unknown outcomes retain capacity and never auto-resend.
- Groq openai/gpt-oss-20b/key saved privately; readiness_missing_or_invalid, zero live calls, disabled. Bounded test authorized but free/account/quota/estimator evidence and durable ledger required. Chat Completions adapter; Responses snippet is not execution evidence.
- URL/child protections retained; test DB/children/snapshots isolated and Groq cleared. Captures recover without refetching; fetch/files outside DB transactions. See url-fetching.md.
- versions.md: immutable versions/revisions, refresh and saved-capture reanalysis; consent/rates/hashes preserved. A -> B -> A retains three versions; context changes rerun rules. Unchanged refresh reuses compatible latest reports; reanalysis creates a revision.

## Latest implementation
- comparisons-deletion.md holds compare/delete contracts and prepared MySQL commands. Compatible reports share engine/mode/profile/provenance; bounded alignment separates text/finding changes without calls. UI includes saved comparisons, confirm/cancel and deletion receipts.
- Deletion rejects active jobs, tombstones, then cleans 50 job folders/pass before claims. Cursor/retry survive file/DB failures; other reports/quotas preserved. OS locks/.deleted barriers fence stale writes. No unknown/reparse/recursive/unattached-job/cache sweep.
- Comparison creation and report publication now lock owner before documents, matching enqueue/deletion. Review found reverse ordering through MySQL owner-FK checks; actual MySQL proof remains pending.
- Eight prepared MySQL checks cover compare/enqueue/publish/delete ordering, refresh/revert, host leases, interruptions/reconnect and a separate test worker. UUID-scoped cleanup, idle-work guards, bounded hidden child, disabled Groq. Unrun; injected faults do not establish actual process-kill acceptance.
- Head/SCHEMA_REVISION remains 20261029_lifecycle; all real migrations unapplied. Docs updated; browser smoke still unexecuted.
- RUNNING.md gives existing-workspace/fresh-clone setup, basic checks, migrations, two-terminal startup, demo and exact push commands. scripts/check.py --quick runs six offline checks without Groq/HTTP/MySQL suites.
- git init -b main succeeded. .gitignore covers secrets/private data/dependencies/build/host/DB/log/key files; .gitattributes preserves frozen bytes. 120 candidate source files checked against configured credentials: no matches, values private.
- git remote add origin failed: cannot lock .git/config (Permission denied). Remote read also hit Git/MSYS NtCreateDirectoryObject access denial. No remote/commit/push completed; do not bypass protection. Normal PowerShell commands are in RUNNING.md.

## Actual checks
- PASS this pass: six quick backend checks (var/tests/baseline-check.txt), frontend build/18 Node tests, compilation/frozen hashes/document caps and Git exclusions. No Groq/provider calls or runtime retries.
- Retain: 66 backend checks after owner-lock fix, five new harness checks and eight-test opt-in guard (var/tests/mysql-harness-check.txt); prior migrations, 181-test baseline and URL/version/lifecycle evidence. MySQL/browser runtime remains pending.

## Deferred facts
- User verified MySQL 8.0.28/MySQL80/3306, both utf8mb4/utf8mb4_bin schemas and scoped accounts/grants (partial_revokes=OFF). Do not recreate accounts. Both app.manage logins returned NOT READY; cause/server password rotation unverified. Diagnose privately, last.
- Private URLs/key preserved; dotenv last definition wins, process environment overrides. .venv Python 3.11.3 requirements/pip check passed (Alembic 1.13.1/PyMySQL 1.1.1/httpx 0.27.0). No extra .env pasting needed.
- Agent HTTP timed out at 45s; socket 10013/browser restrictions persist. User WordPress HTTPS/Groq TCP passed. Retry only after changed conditions. Fresh npm ci, real MySQL/HTTP/browser acceptance pending.
- WordPress: var/eval/sources/wordpress-terms/8486343d2107439f860e17420a50dfd1; hashes verified, October 6, 2026/CC BY-SA 4.0 observed. Rights review pending. var/eval/reviews/wordpress-review-v1.json has 417 candidates, zero labels. See eval/README.md.
- Preserve var archives/caches: prior cleanup auto-rejected, sandbox approvals disabled. No working.md or duplicate handoff.

## Pending work
- Finish authorized push from a terminal able to write .git/config and run Git helpers; preserve remote history, no force push. Then follow RUNNING.md. Database login/migrations still block a verified running app.
- Gate 5 benchmark/evaluation tooling, human labels/free-provider evidence and final MySQL/API/worker/restart/browser acceptance remain required. Latest user prioritized sharing baseline first; no acceptance scope was removed.

## Reproduction
- Root: .venv/Scripts/python.exe scripts/check.py --quick; npm.cmd --prefix apps/web run build; npm.cmd --prefix apps/web test. Run/push instructions: RUNNING.md. Full checks: scripts/check.py; deferred MySQL commands: comparisons-deletion.md.
