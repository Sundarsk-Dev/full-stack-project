# TermsLens

React/Vite/TypeScript, FastAPI and MySQL: private accounts, queued paste/URL analysis, guarded discovery, immutable captures/reports and eight rule groups. Groq has consent/provenance and a tested adapter; it stays disabled until readiness is verified.

**Baseline implemented; final acceptance pending.** Start with [Windows run instructions](RUNNING.md). Migrations, runtime/provider acceptance and evaluation remain open. See the [checklist](verification-checklist.md), [debugging guide](architecture-debugging.md), frozen [plan](plan-proposal.md), [workflow](implementation-workflow.md) and [handoff](transfercontext.md).

## Current scope

- Register/login/logout; HttpOnly sessions, origin/CSRF checks and bounded login/submission rates.
- Paste one English policy or enter a direct public URL; owned idempotent jobs, stage polling, expiring leases, three attempts and atomic publication. Changing input/type resets Groq consent.
- Initial rules for sharing/selling, retention/deletion, tracking/consent, content licensing/AI training, renewal/refunds, changes to terms, arbitration and termination/export. Unfamiliar, conflicting or qualified wording abstains; unsupported evidence remains unknown.
- Score bounds/coverage, Unicode evidence and private history. Balanced/privacy-first views use saved evidence and the same scoring module as publication; the selector also applies to the rules baseline. Rules/fixtures remain engineering examples.
- [URL workflow](url-fetching.md): choose Public URL; for a homepage, Find policy links, select one, confirm its type, then Analyze policy. Discovery never submits a job. Blocked/dynamic pages offer paste fallback. Private `var/snapshots` captures survive worker restart without refetching; reports show saved URL/time/hash metadata.
- Request/job correlation, fixed diagnostic errors and read-only environment/job inspection support debugging without logging private input.
- [Versions and reanalysis](versions.md): open a report to refresh its URL/replace text, browse earlier captures/revisions or reanalyze saved text. Unchanged refreshes reuse compatible reports; A -> B -> A retains all versions. Reanalysis creates a new report revision.
- [Comparisons and deletion](comparisons-deletion.md): compare compatible versions/policies with separate text/finding changes. Confirm deletion to hide a document; the running worker removes its reports, dependent comparisons and snapshots, retrying interrupted cleanup. Status remains available after restart.
- Rules-only sends no text externally. The coordinator joins consent, durable quotas, response recovery/validation and code scoring; publication verifies each AI finding against its owned saved response. Reports show requested/effective mode, degradation, source/model and a stored rules baseline.
- The API accepts `mode: "hybrid"` only with boolean `consent: true` and `provider: "groq"`; it timestamps consent. The worker checks expiring local readiness before using the bounded Groq adapter. Disabled/unverified configuration produces explicit rules-only fallback. `/api/health` exposes availability/reason and disables hybrid in the UI when unavailable. Fake HTTP responses exercise persistence; they do not prove live readiness.

## Windows prerequisites and MySQL

Use Python 3.11, Node 22.12+ and MySQL 8. The application accepts only `mysql+pymysql` URLs targeting `termslens_db`; test commands accept only `termslens_test`. SQLite appears solely in supplementary tests.

Run in a normal local PowerShell terminal, from the project root:

```powershell
Get-Service -Name '*mysql*'
mysql --version
Test-NetConnection -ComputerName 127.0.0.1 -Port 3306
```

MySQL80 is running and port 3306 is reachable. After saving/replacing both URLs, the user's two application login checks returned `NOT READY`; the cause is unverified. At the user's request, database troubleshooting/migrations are deferred to the final runtime verification pass.

Using your existing MySQL administrator login, run this at the `mysql>` prompt (or in Workbench) to create and inspect the two isolated schemas:

```sql
CREATE DATABASE IF NOT EXISTS termslens_db CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE DATABASE IF NOT EXISTS termslens_test CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
SELECT VERSION() AS mysql_version;
SELECT SCHEMA_NAME, DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME
FROM INFORMATION_SCHEMA.SCHEMATA
WHERE SCHEMA_NAME IN ('termslens_db', 'termslens_test');
```

Expect MySQL 8+ and both schemas using `utf8mb4`. Existing schemas are preserved; inspect differing settings before changing them. These results contain no passwords. Schema creation alone does not establish the application's login or migration readiness.

Both local accounts and scoped grants are now created. For a fresh setup with unused account names and `partial_revokes=OFF`, [mysql_accounts.sql](scripts/mysql_accounts.sql) creates them with generated passwords. Escaped underscores make schema names literal. Keep password output private; do not rerun account creation for existing accounts.

```powershell
if (!(Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r apps/api/requirements-dev.txt
.venv/Scripts/python.exe scripts/configure_database.py
```

The helper hides password input and encodes passwords inside the two URLs in private `.env`; no separate password variable or MySQL administrator password is needed. It appends missing settings and preserves existing values, including your Groq key. `.env.example` leaves URLs blank so copied settings still trigger prompts. No network calls or secret output.

If passwords were shared, rotate both using your MySQL administrator session; keep the generated output private:

```sql
ALTER USER 'termslens'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD;
ALTER USER 'termslens_test'@'127.0.0.1' IDENTIFIED BY RANDOM PASSWORD;
```

Then enter the new passwords locally:

```powershell
.venv/Scripts/python.exe scripts/configure_database.py --replace-passwords
```

This flag appends password overrides for the documented local accounts; dotenv uses the last definition. Other URL fields and existing `.env` bytes are preserved. Process environment values override `.env`; clear stale URL variables locally if set. Variables:

| Variable | Purpose |
|---|---|
| TERMSLENS_DATABASE_URL | MySQL application login and `termslens_db`, with `charset=utf8mb4`. |
| TERMSLENS_TEST_DATABASE_URL | Separate test URL for `termslens_test`. |
| TERMSLENS_ALLOWED_ORIGINS | Comma-separated exact UI/API origins; defaults cover local ports 5173/8000. |
| TERMSLENS_SECURE_COOKIES | `false` for local HTTP; `true` when serving through HTTPS. |
| GROQ_API_KEY | Private key in local `.env` or process environment; never in examples, commands or reports. |
| TERMSLENS_GROQ_MODEL | Exact model ID verified for the account; no default or automatic substitute. |
| TERMSLENS_GROQ_ENABLED | Defaults to `false`; keep disabled until readiness and scoped live-call authorization are established. |
| TERMSLENS_GROQ_READINESS_FILE | Private readiness JSON; default `var/groq-readiness.json`, relative to the project root. |

Prove both logins, then stop API/worker before applying migrations to each schema:

```powershell
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.manage
../../.venv/Scripts/python.exe -m app.manage --test
Pop-Location
.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade head
.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini -x test=true upgrade head
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.manage --migrations
../../.venv/Scripts/python.exe -m app.manage --test --migrations
Pop-Location
```

These checks print no credentials. Do not point tests at an unrelated database. Migrations do not run automatically on API startup.

## Groq readiness and authorization

This session authorizes a bounded Groq test. User-selected `openai/gpt-oss-20b` is saved; the key is preserved. Test-enable preflight returns `readiness_missing_or_invalid`; verified free access/quotas remain required. Groq is disabled. Process environment overrides `.env`. The existing `httpx==0.27.0` adapter uses Chat Completions; the supplied Responses API example is not a live test result.

The [readiness example](groq-readiness.example.json) is deliberately invalid. When verified evidence is available, prepare a private copy without replacing an existing record:

```powershell
if (!(Test-Path -LiteralPath 'var/groq-readiness.json')) { Copy-Item -LiteralPath 'groq-readiness.example.json' -Destination 'var/groq-readiness.json' }
```

Fill this UTF-8 JSON from actual account/model evidence; do not invent limits or set attestations merely to enable the UI:

| Field | Required evidence / validation |
|---|---|
| `schema_version`, `model`, `account_id` | Version `1`; model exactly matches configuration. Account ID is lowercase SHA-256 of a stable nonsecret account identifier, never the API key. Verify that the key belongs to that account; retain identity across key rotation. |
| `verified_at`, `valid_until` | Timezone-aware UTC timestamps: verified time <= now < expiry, with expiry at most 24 hours after verification. Reverify before renewal. |
| `free_access_verified` | Current model availability, no-billing terms and account settings have been checked. |
| `exclusive_account_usage_verified` | This ledger accounts for all usage of the account. Account for earlier usage before first dispatch, such as by observing the relevant reset/quiet window. |
| `limits_complete_verified`, `limits` | Every applicable account/model request and token limit, including daily limits. Each has a stable unique `name`, `resource`, `period_seconds` and positive `capacity`. |
| `estimator_verified`, `estimator_revision` | Verify `utf8-json-plus-256-v1` against the chosen model's tokenization/framing. This byte estimate plus 256 is not a measured tokenizer count; unverified accounting permits zero calls. |
| `verification_reference` | Nonempty reference to dated evidence without keys, private account identifiers or policy bodies. A reference alone does not prove the evidence. |

Records are limited to 16 KiB and 1-16 limits. Resources are `requests`, `total_tokens`, `input_tokens`, `output_tokens`; require requests plus total tokens or both input/output tokens. Periods are 1 second to 31 days; capacities are integers from 1 to 1,000,000,000. Unknown fields/types, duplicate names, incomplete attestations, mismatched model/estimator or expired evidence fail closed. Keep account/quota names and persisted ledger rows stable; never reset budgets to regain capacity.

Only after readiness evidence and scoped live-call authorization exist may the normal worker be enabled. Restart API and worker after environment changes; readiness JSON is reread on health checks and each hybrid claim, and reservations enforce its expiry. `configured` means local validation passed, not live acceptance. Per-job Groq consent is still required. Rules-only launches no provider process. `app.worker --test` and `app.manage --test` always clear provider settings and use only the test database, even when production Groq is enabled.

Transport sends once to the fixed Groq endpoint with no redirects, environment proxies or internal retries. A parent deadline kills/reaps stalled children; an independent child watchdog bounds a send if the worker is stopped. Responses have 128-KiB envelope/64-KiB content caps. Unknown outcomes keep reservations and are never automatically resent; only a known 429 can use the coordinator's one retry with <=2 seconds wait. Both attempts share the two-request/12-clause/10,000-input/2,000-output ceilings.

From `apps/api`, `python -m app.manage --diagnostics` reports safe configuration status without connecting. It exits successfully even when unavailable; inspect `external_review.available` and `reason`. `python -m unittest tests.test_groq -v` exercises fake HTTP, configuration, test isolation and real local child lifetimes without provider calls. G2-05/G2-07 stay pending until model accounting, free access and authorized live behavior are observed.

## Evaluation sources

[Source preparation](eval/README.md) covers the user-selected online examples, human annotation, family splits, reuse rights and Groq evidence. The candidate catalog proposes eight families and 112 base clauses, including 42 heldout clauses; these are targets, not collected or labeled data.

```powershell
python scripts/collect_eval_sources.py
```

This lists candidates without connecting. WordPress is captured; its offline worksheet has 417 candidates and zero labels. `scripts/prepare_eval_review.py` checks source hashes, offsets and split duplicates. Follow the guide for remaining sources, rights and human review.

## Build and run

```powershell
Push-Location apps/web
npm.cmd ci --cache ../../var/npm-cache
npm.cmd run build
Pop-Location
```

Cached frontend dependencies build; fresh `npm ci` remains unverified. Python requirements and `pip check` passed in `.venv`. The handoff records affected checks; runtime acceptance remains separate.

Start the API in one terminal:

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000
```

Start exactly one worker in a second terminal:

```powershell
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.worker
```

Open http://127.0.0.1:8000 for the built UI. Readiness is `/api/health`; API documentation is `/api/docs`. The worker uses a MySQL advisory lock plus per-job leases; queued jobs persist across restarts.

For frontend development, run `npm.cmd --prefix apps/web run dev` and open http://127.0.0.1:5173. Vite proxies `/api` to port 8000; the API and worker still need to run.

## Verification

From the project root, after dependency installation:

```powershell
.venv/Scripts/python.exe scripts/check.py --quick
.venv/Scripts/python.exe scripts/check.py --http --mysql
npm.cmd --prefix apps/web test
npm.cmd --prefix apps/web run build
```

Default checks cover core contracts and the local recovery harness. `--http` adds HTTP tests; `--mysql` requires migrated `termslens_test`, checks workers/quotas and the [eight prepared race/recovery cases](comparisons-deletion.md#prepared-mysql-acceptance). Stop test workers first; active jobs/pending deletions are preserved and rejected. Fixtures disable Groq and clean only their own rows/files. Real MySQL/browser acceptance remains pending.

The optional `apps/web/tests/browser.smoke.cjs` uses an existing Playwright Node runtime and browser installation. Set `TERMSLENS_PLAYWRIGHT_MODULE` to the existing module path and, if needed, `PLAYWRIGHT_BROWSERS_PATH` to its browser cache; run `node apps/web/tests/browser.smoke.cjs`. It checks the UI with explicitly mocked API responses; it is not database acceptance. No browser/tool installation is automatic.

Follow G1-09 through G1-12 in the [checklist](verification-checklist.md) for real browser submission, Unicode evidence, restart, two-user isolation and debugging/accessibility. [gate1.txt](eval/fixtures/gate1.txt) exercises the original three groups; [eight-groups.txt](eval/fixtures/eight-groups.txt) exercises the expanded rules. Both are engineering fixtures, not human-heldout evaluation.

## Diagnose a failure

From the project root:

```powershell
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.manage --diagnostics
../../.venv/Scripts/python.exe -m app.manage --migrations
../../.venv/Scripts/python.exe -m app.manage --job <job-uuid>
Pop-Location
```

Use an actual `<job-uuid>`. `--diagnostics` does not connect; `--migrations` checks head `20261029_lifecycle`; `--job` reads safe state/check/reuse metadata. `--test` selects the isolated schema and clears Groq. Stop API/worker before real migrations. [Offline DDL](versions.md#migration-and-checks) does not establish MySQL readiness.

Trace request/job IDs and worker events using the [debugging guide](architecture-debugging.md#trace-one-failure). Keep bodies, credentials and tokens out of logs. The checklist and handoff track acceptance.
