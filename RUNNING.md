# Run TermsLens on Windows

The user confirms registration and a working request/output. Both MySQL migrations passed at `20261029_lifecycle`. Output readability is a known UI issue; detailed provider/recovery acceptance remains pending. [README](README.md#start-here-existing-windows-workspace) starts the AI demo; the basic run below uses Rules-only.

Run commands from the project root. This workspace already has `.venv`, frontend dependencies, a built frontend and private `.env`; proceed directly to step 3. Retain the passing checks/setup below for reproduction; do not recreate database accounts or overwrite `.env`.

## 1. Basic checks

```powershell
.venv/Scripts/python.exe scripts/check.py --quick
npm.cmd --prefix apps/web test
npm.cmd --prefix apps/web run build
```

The quick command checks eight-group rules, Unicode evidence, saved profiles/reports, comparison reopening and deletion using six offline tests. It makes no Groq calls and does not run the Groq or MySQL suites.

## 2. Verify the application database

Stop any existing API/worker processes first. Set Groq off for the current terminal and check the saved application login:

```powershell
$env:TERMSLENS_GROQ_ENABLED = 'false'
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.manage
Pop-Location
```

Continue only if it prints `PASS`. Failures now include a fixed code without credentials or raw driver messages. Your saved URLs contain the passwords; no separate administrator password is needed.

| Failure code | Next step |
|---|---|
| `mysql_1045` | Verify the existing app account/password locally; then use `scripts/configure_database.py --replace-passwords` from the root. |
| `mysql_2003` / `mysql_2002` | Check MySQL service and the host/port in the private URL. |
| `socket_access_denied` | Windows blocked the connection; use your normal local terminal and inspect host restrictions. |
| `mysql_1044` / `mysql_1049` | Check the account's database grants / whether the configured schema exists. |
| `schema_missing` / `schema_outdated` | Stop API/worker and apply the migrations below. |
| `database_configuration` | Check URL syntax, `mysql+pymysql`, and the intended database name. |

Use the [database instructions](README.md#windows-prerequisites-and-mysql) for setup. `--diagnostics` checks installed packages/configuration only; `database_configured: true` does not verify a login.

Apply the application migrations, stopping if either command fails:

```powershell
.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade head
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.manage --migrations
Pop-Location
```

Expected schema: `20261029_lifecycle`. The separate test database is needed only for real MySQL acceptance; use the README block to migrate and verify it too. Never point test commands at the application database.

## 3. Start the application

Terminal 1, from the project root:

```powershell
$env:TERMSLENS_GROQ_ENABLED = 'false'
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000
```

Terminal 2, from the project root:

```powershell
$env:TERMSLENS_GROQ_ENABLED = 'false'
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.worker
```

Keep both terminals running; run exactly one worker. It prints `Worker started. Waiting for jobs`; staying at that screen is normal. Ctrl+C stops it. Open **http://127.0.0.1:8000**; the API serves the built frontend. For editing, optionally run `npm.cmd --prefix apps/web run dev` in a third terminal and open http://127.0.0.1:5173.

## 4. Basic manual demo

1. Register, choose **Rules-only**, use **Fill an example**, and submit.
2. Wait for completion, open the report and highlight evidence.
3. Reopen it from history; replace the text, then compare the saved reports.
4. Confirm deletion of the example and wait for its deletion receipt. Leave the worker running for cleanup.
5. Log out. Use Ctrl+C in the API and worker terminals to stop them.

Registration and request/output are user-reported working. Follow [README output guidance](README.md#submit-and-read-the-output) for scores, filters and evidence. Reopening, precise provider provenance, recovery, evaluation and benchmarks remain in the [checklist](verification-checklist.md); unreadable output is an open UI issue.

## Fresh clone only

Use Python 3.11, Node 22.12+ and MySQL 8. The application accepts only `mysql+pymysql` URLs targeting `termslens_db`; test commands accept only `termslens_test`. SQLite appears solely in supplementary tests.

Run in a normal local PowerShell terminal, from the project root:

```powershell
Get-Service -Name '*mysql*'
mysql --version
Test-NetConnection -ComputerName 127.0.0.1 -Port 3306
```

On 2026-10-30, both application/test logins and migration checks passed in the user's terminal: MySQL 8.0.28, intended databases, utf8mb4, revision `20261029_lifecycle`. Database setup is complete for this workspace; proceed to startup. API/worker/browser acceptance remains pending.

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

Install and build the frontend from the root:

```powershell
Push-Location apps/web
npm.cmd ci --cache ../../var/npm-cache
npm.cmd run build
Pop-Location
```

Then use the startup commands at the top of README. Private Groq evidence is machine/account-specific; a clone starts with Rules-only until readiness is established.

## Git updates

Branch `main` and remote `origin` already exist. Use the [README Git commands](README.md#git-save-and-push-updates) to review, commit and push. Do not reinitialize this repository or recreate its remote. Private configuration/data and generated outputs stay out of Git.
