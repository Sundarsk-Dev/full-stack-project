# TermsLens AI demo

Both MySQL schemas are migrated. The user reports registration and request/output working, but the report UI is hard to understand. The worker must stay open; it announces startup and handles Ctrl+C cleanly. [README](README.md#submit-and-read-the-output) explains existing output controls.

The working request is user-reported; its saved mode/model/usage have not been inspected, so full live-provider acceptance remains open. The saved key's free account and quiet usage were confirmed before setup. Limits for `openai/gpt-oss-20b`: 30 requests/minute, 1,000/day, 8,000 tokens/minute, 200,000/day. No key needs to be pasted again.

## One-time AI setup

Skip this section when already configured. For missing readiness, stop API/worker with Ctrl+C and run from the root in normal PowerShell. Current saved account evidence is required:

```powershell
.venv/Scripts/python.exe -m pip install openai-harmony
.venv/Scripts/python.exe scripts/configure_groq_demo.py
```

The optional [official Harmony tokenizer](https://developers.openai.com/cookbook/articles/openai-harmony) verifies the selected model's no-tool framing and demo token estimate. Its installation/download needs networking. Setup uses the existing key for one read-only Groq model lookup, sends no inference, and prints no credentials. It writes `var/groq-readiness.json` and appends the enable flag to `.env` only after verification. Existing credentials/records/ledger stay intact.

The record expires within four hours and no later than 24 hours after account confirmation. Setup does not silently renew it; review account usage before renewal. Use this Groq account only through TermsLens during the demo so the ledger accounts for all usage. Provider-specific framing and actual usage still need the live result; official token counts alone do not pass hybrid acceptance.

If setup prints `NOT READY`, use that fixed error to diagnose it. Do not set verification flags manually or overwrite an existing record. The agent's earlier socket restriction is separate from the user's working terminal.

## Start and paste once

Terminal 1, from the project root:

```powershell
$env:TERMSLENS_GROQ_ENABLED = 'true'
.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000
```

Terminal 2, from the project root:

```powershell
Get-Content -LiteralPath './eval/fixtures/wordpress-terms-demo.md' -Raw -Encoding UTF8 | Set-Clipboard
$env:TERMSLENS_GROQ_ENABLED = 'true'
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.worker
```

Keep both open. Expect `Worker started. Waiting for jobs; keep this terminal open. Ctrl+C stops it.` The prompt returns only when the worker stops. Use one normal worker: `--test` intentionally clears Groq configuration.

Refresh **http://127.0.0.1:8000**, log in, choose **Terms**, **Paste text**, **Hybrid**, and its consent checkbox. Paste and analyze once. If Hybrid is unavailable, run `../../.venv/Scripts/python.exe -m app.manage --diagnostics` from `apps/api`; inspect `external_review.reason`. Restart both processes after configuration changes.

[wordpress-terms-demo.md](eval/fixtures/wordpress-terms-demo.md) contains a complete 52-word refunds paragraph from the WordPress.com terms captured on 29 October 2026. Source: https://wordpress.com/tos/; license: https://creativecommons.org/licenses/by-sa/4.0/. Only selection/Markdown formatting changed. It is an attributed demo excerpt, not a full contract or a human-labeled evaluation. Offline preparation fits exactly one AI batch.

Check **Saved result**, review status and model provenance, then reopen from history. Degraded rules-only is not AI success; zero accepted findings can be abstention or validation rejection. Retain the job/report ID; do not repeatedly resubmit a timeout. Detailed evidence/reopen/recovery, live provider acceptance and Gate 5 remain pending; the basic request/output is user-observed.
