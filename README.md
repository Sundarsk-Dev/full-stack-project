# TermsLens

[Mini project report: PDF and editable Word](reports/README.md)

React/Vite/TypeScript, FastAPI and MySQL. **Reports now open with a score overview, concerns, favorable findings and unresolved clauses.** Hybrid is the default when available; sending text still needs consent. Both databases are migrated. Full runtime/provider acceptance and evaluation remain open in the [checklist](verification-checklist.md).

## Start here: existing Windows workspace

No reinstall or database reset is needed for the working workspace. In **each** PowerShell terminal, go to the project root:

```powershell
Set-Location -LiteralPath 'D:\programming\snu chennai\sem 1\full stack programming\mini project'
```

**Terminal 1 — API and built UI:**

```powershell
$env:TERMSLENS_GROQ_ENABLED = 'true'
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000
```

**Terminal 2 — one worker:**

```powershell
$env:TERMSLENS_GROQ_ENABLED = 'true'
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.worker
```

Leave both terminals open. `Worker started. Waiting for jobs…` means it is running. Open **http://127.0.0.1:8000**. Ctrl+C stops each process; do not stop MySQL or delete saved data. Groq needs a current readiness record as well as the enable flag; [AI setup/recovery](DEMO.md) explains it. For a local-only run, set the flag to `false` in both terminals and choose Rules-only.

## Submit and read the output

1. Register or log in. Choose the correct policy type, give the report a recognizable title, and select Paste text.
2. In a third terminal at the project root, copy the sample below, then paste into the app. **Hybrid** is selected when available; check consent for AI, or choose **Rules-only** to keep text local.
3. Click **Analyze policy** once. Leave the worker open. The completed report replaces the input form and opens at its overview.
4. Use **Your history** to reopen reports or **New analysis** to return to the form. Filters, profiles, baseline switching and reopening make no AI calls.

```powershell
Get-Content -LiteralPath './eval/fixtures/consumer-demo.md' -Raw -Encoding UTF8 | Set-Clipboard
```

The **434-word synthetic consumer demo** gives 14 rule findings (nine concerns/five protections); six clauses fit AI review. See [expected output](DEMO.md#consumer-walkthrough). Use **Terms**. This is an engineering example, not measured accuracy.

| Output | How to interpret it |
|---|---|
| Requested / Saved result | Requested is your choice; Saved result is what actually ran. Hybrid requested with Rules-only saved means fallback. |
| Score range / 100 | **Higher is more favorable.** Unknowns widen the range. Zero rated clauses show **Not enough evidence**, with the raw bounds under How to read this score. |
| Grade or no grade | A letter needs at least 80% clause coverage, consistent scope and both score bounds in the same grade band. No grade means insufficient evidence. |
| Clause coverage | Percentage of substantive clauses with an accepted finding, not measured accuracy. |
| Severity / 5 | **Higher is more concerning:** favorable 0, neutral 1, concerning 2–3, invasive 4–5. This runs opposite to the overall score. |
| Unknown / Unrated | No supported interpretation; it does not mean safe. Read the clause and its explanation. |
| Finding source / Model | Distinguishes rule matches, reviewed exact matches and AI findings; AI findings show recorded model provenance. Zero accepted AI findings alone does not prove a provider failure. |

Start with **Needs attention** and **Favorable findings**. **Inspect evidence** opens the exact quote and surrounding source in a dialog; Escape closes it and restores focus. **Explore the findings** separates concerns, favorable, neutral and unresolved clauses. Group buttons reset narrower filters; advanced filters sit under **Filter by category, rating or source**. Longer reports use eight clauses per page.

Open **Score profile, review details and category breakdown** for the saved rules-only baseline, Privacy-first weights and provenance. Expand **Versions and reanalysis** or **Compare saved reports** below the report when needed. Reanalysis can request AI again; comparing compatible reports makes no AI calls. **Manage this document** contains confirmed deletion; keep the worker running for cleanup.

Desktop/mobile Playwright checks pass. Per-request input is now 6,000; two calls still share 10,000 input/2,000 output and account quotas. The 97-clause policy remains context-limited; the demo does not establish general-policy coverage.

## Current scope

- Register/login/logout; HttpOnly sessions, origin/CSRF checks and bounded login/submission rates.
- Paste one English policy or enter a direct public URL; owned idempotent jobs, stage polling, expiring leases, three attempts and atomic publication. Changing input/type resets Groq consent.
- Initial rules for sharing/selling, retention/deletion, tracking/consent, content licensing/AI training, renewal/refunds, changes to terms, arbitration and termination/export. Unfamiliar, conflicting or qualified wording abstains; unsupported evidence remains unknown.
- Score bounds/coverage, Unicode evidence and private history. Balanced/privacy-first views use saved evidence and the same scoring module as publication; the selector also applies to the rules baseline. Rules/fixtures remain engineering examples.
- [URL workflow](url-fetching.md): choose Public URL, or Find policy links and select one; confirm type, then Analyze policy. Discovery does not submit a job. Blocked/dynamic pages offer paste fallback. Captures preserve source/time/hash metadata.
- Request/job correlation, fixed diagnostic errors and read-only environment/job inspection support debugging without logging private input.
- [Versions/reanalysis](versions.md) preserve immutable captures and report revisions, including A -> B -> A; compatible unchanged reports can be reused.
- [Comparisons/deletion](comparisons-deletion.md) separate text/finding changes and support recoverable worker cleanup of confirmed deletions.
- Rules-only sends nothing externally. Hybrid requires timestamped Groq consent, readiness, durable quotas and validated provenance. Reports preserve requested/effective mode, fallback reason and rules baseline. `/api/health` exposes availability; unavailable readiness disables Hybrid in the UI.

## Windows prerequisites and MySQL

Use Python 3.11, Node 22.12+ and MySQL 8. [Fresh-clone instructions](RUNNING.md#fresh-clone-only) cover schemas/scoped accounts, hidden password entry, dependencies, migrations and frontend build. This workspace already passed both login/migration checks at `20261029_lifecycle`; skip setup during normal startup. Tests use only `termslens_test`; the app uses `termslens_db`.

Keep the database URLs and Groq key in private `.env`; the password helper preserves existing settings. Process environment overrides `.env`. The source [`.env.example`](.env.example) contains placeholders only. Never paste credentials into Git commands or reports.

## Groq readiness and authorization

Live testing is authorized; readiness renewal sent no inference. Latest inspected report: limited/Rules-only, zero AI provenance. Skip setup when configured.

For an unconfigured demo only, stop API/worker and run the setup below. It requires current saved account evidence. The optional official tokenizer is a setup tool, not an application dependency:

```powershell
.venv/Scripts/python.exe -m pip install openai-harmony
.venv/Scripts/python.exe scripts/configure_groq_demo.py
```

Setup checks model/tokenizer and enables AI until account confirmation +24 hours, without inference or usage resets. To renew an older expired record within that window, run from the root and refresh:

```powershell
.venv/Scripts/python.exe scripts/configure_groq_demo.py --renew
```

Renewal rechecks model/tokenizer and archives the old record, preserving account/usage. It cannot extend expired account evidence. The UI explains failures; see [recovery](DEMO.md#when-ai-becomes-unavailable). [Example](groq-readiness.example.json) remains invalid; manual records require evidence:

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

Enable the worker only with readiness and live-call authorization. Restart API/worker after environment changes. Health/hybrid claims reread readiness; reservations enforce expiry. `configured` is local validation, not live acceptance. Per-job consent is required. Rules-only launches no provider; worker/manage `--test` clear provider settings and use only the test database.

Transport sends once to the fixed Groq endpoint with no redirects, environment proxies or internal retries. A parent deadline kills/reaps stalled children; an independent child watchdog bounds a send if the worker is stopped. Responses have 128-KiB envelope/64-KiB content caps. Unknown outcomes keep reservations and are never automatically resent; only a known 429 can use the coordinator's one retry with <=2 seconds wait. Both attempts share the two-request/12-clause/10,000-input/2,000-output ceilings.

From `apps/api`, `python -m app.manage --diagnostics` reports safe configuration status without connecting. It exits successfully even when unavailable; inspect `external_review.available` and `reason`. `python -m unittest tests.test_groq -v` exercises fake HTTP, configuration, test isolation and real local child lifetimes without provider calls. G2-05/G2-07 stay pending until model accounting, free access and authorized live behavior are observed.

## Evaluation sources

[Source preparation](eval/README.md) covers the user-selected online examples, human annotation, family splits, reuse rights and Groq evidence. The candidate catalog proposes eight families and 112 base clauses, including 42 heldout clauses; these are targets, not collected or labeled data.

```powershell
python scripts/collect_eval_sources.py
```

This lists candidates without connecting. WordPress is captured; its offline worksheet has 417 candidates and zero labels. `scripts/prepare_eval_review.py` checks source hashes, offsets and split duplicates. Follow the guide for remaining sources, rights and human review.

## Build and run

Startup commands are at the top. After frontend edits, run `npm.cmd --prefix apps/web run build` and refresh port 8000. For live editing, use `npm.cmd --prefix apps/web run dev` in a third terminal and open http://127.0.0.1:5173; API/worker remain required. Health: `/api/health`; API docs: `/api/docs`. [Fresh setup](RUNNING.md#fresh-clone-only) includes dependency installation.

## Git: save and push updates

This workspace already has branch `main` and remote `origin`; do not rerun `git init` or `git remote add`. From the root, run each step only if the previous one succeeds:

```powershell
git status --short
git check-ignore -v .env var/groq-readiness.json var/groq-demo-evidence.json
git add .gitignore .gitattributes
git add .
git diff --cached --stat
git diff --cached --check
git commit -m "Document demo workflow and improve worker startup"
git push -u origin main
```

Review the staged filenames before committing. `.gitignore` excludes private environments/evidence/captures, dependencies, builds, caches, test reports and local database/key files; source fixtures, lockfiles and examples remain included. Ignore rules do not remove files already tracked. Never force-add secrets or force-push over remote work. If the push is rejected, preserve local changes and resolve the reported remote-history/authentication issue before retrying.

Repository: https://github.com/Sundarsk-Dev/full-stack-project. A push shares code; it does not deploy the app. Current push status is unverified. [Fresh-clone setup](RUNNING.md#fresh-clone-only) is required on another machine; private `.env`, readiness evidence and local data are not transferred by Git.

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
