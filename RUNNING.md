# Run TermsLens on Windows

The baseline code is implemented. Real MySQL login/migrations and browser acceptance are still pending; previous database login checks returned `NOT READY`. A Git push shares source code; it does not deploy a running website. Groq stays disabled for this basic run.

Run commands from the project root. This workspace already has `.venv`, frontend dependencies and private `.env`; do not recreate the database accounts or overwrite `.env`.

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

Continue only if it prints `PASS`. If it prints `NOT READY`, the blocker is database setup/login, not the React build. Use the [database instructions](README.md#windows-prerequisites-and-mysql); keep password/key output private. Your saved URLs already contain the passwords; do not add a separate administrator password.

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

Keep both terminals running; run exactly one worker. Open **http://127.0.0.1:8000**. The API serves the built frontend. For frontend editing, optionally run `npm.cmd --prefix apps/web run dev` in a third terminal and open http://127.0.0.1:5173; the API and worker are still required.

## 4. Basic manual demo

1. Register, choose **Rules-only**, use **Fill an example**, and submit.
2. Wait for completion, open the report and highlight evidence.
3. Reopen it from history; replace the text, then compare the saved reports.
4. Confirm deletion of the example and wait for its deletion receipt. Leave the worker running for cleanup.
5. Log out. Use Ctrl+C in the API and worker terminals to stop them.

These manual checks remain pending until observed. Human evaluation, benchmarks and live Groq acceptance are separate requirements in the [checklist](verification-checklist.md).

## Fresh clone only

Install Python 3.11, Node 22.12+ and MySQL 8, then:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r apps/api/requirements-dev.txt
npm.cmd --prefix apps/web ci --cache ../../var/npm-cache
if (!(Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
.venv/Scripts/python.exe scripts/configure_database.py
```

Create the two scoped database accounts/schemas using the README first if this is a new machine. No Groq key is required for rules-only. `.env`, `var/`, dependencies and build output stay out of Git; install dependencies and rebuild after cloning.

## Push this baseline

Repository: https://github.com/Sundarsk-Dev/full-stack-project. Local `main` is initialized. This agent session could not write `.git/config` or launch Git's remote helper, so no remote, commit or push was completed. Run in your normal PowerShell terminal from the project root, stopping if a command fails:

```powershell
git remote add origin https://github.com/Sundarsk-Dev/full-stack-project.git
git add .gitignore .gitattributes
git add .
git status --short
git commit -m "Add TermsLens baseline application"
git push -u origin main
```

`.gitignore` is prepared first: private environment/captures, dependencies, build output, host metadata, database/log files and key files are excluded. `.gitattributes` preserves the frozen source bytes across clones. The candidate source files were checked against configured local credentials without printing them. Keep the push without `--force`; an existing remote history must be preserved.
