# TermsLens AI demo

Both MySQL schemas are migrated. Reports now open at a score overview with concerns, favorable findings and unresolved clauses. Hybrid defaults on when available; consent remains unchecked. The worker must stay open. [README](README.md#submit-and-read-the-output) explains the new output controls.

## Consumer walkthrough

Use [consumer-demo.md](eval/fixtures/consumer-demo.md): an authored, synthetic 434-word example with 20 substantive clauses across the eight project categories. It deliberately includes supported engineering-rule wording and six longer clauses for AI review. It is not a real company's policy, human-labeled evaluation or evidence of general accuracy. Copy only that file into the app; this explanatory text is outside the policy.

Restart API and worker to load the new allowance using the commands below. From the project root, copy the example:

```powershell
Get-Content -LiteralPath './eval/fixtures/consumer-demo.md' -Raw -Encoding UTF8 | Set-Clipboard
```

Choose **Terms**, title it **Consumer demo**, select **Hybrid**, check consent and analyze once. The offline baseline has **14 rated clauses: nine concerns and five favorable findings, 70% coverage, score range 0–42.5, no letter grade**. AI may rate or abstain on the six remaining clauses; do not promise its output before observing it.

| Consumer question | What to inspect |
|---|---|
| Can my information reach advertisers? | No-sale is favorable, but a separate rule flags advertiser sharing. “Not sold” does not mean “not shared.” |
| What protections are stated? | Deletion within 30 days, consent before advertising cookies, cancellation anytime and export before closure. Each has exact evidence. |
| Can the service keep using my content? | Perpetual licensing and AI training are flagged; inspect the permission rather than assuming upload grants only temporary storage. |
| Can I cancel and recover my payment? | Cancellation is favorable; automatic renewal and non-refundable payments remain separate concerns. Cancellation is not a refund promise. |
| What could change or be lost? | Changes/termination without notice, mandatory arbitration and a class-action waiver are flagged. |
| What should AI add? | Review partner transfers after opt-out, the essential-cookie description, continued access after cancellation, the introductory price doubling, excluded export content and the suspension appeal process. These are review questions, not preassigned model answers. |

Use the report's concern/favorable groups and **Inspect evidence**. Under review details, compare the rules baseline with the saved Hybrid result and check AI source/model labels. New findings can add useful detail without producing a letter grade; unresolved clauses remain visible.

The per-request input reservation is now **6,000** (previously 5,000); the two requests still share **10,000 input / 2,000 output**, at most twelve clauses and actual account quotas. These are conservative reservations, not measured token usage. Offline packing is four plus two clauses, reserving 5,370 + 3,445 input; no live inference was sent for this check. A first request above 5,000 may leave insufficient total capacity for a retry. Provider limits can still prevent a second request.

The earlier 97-clause policy still cannot fit: global exception collection and narrow exact rules need separate work. This walkthrough demonstrates a bounded consumer review and does not claim that general-policy limitation is fixed.

Inspected full-policy reports ended limited/Rules-only, with zero AI findings/provenance. Live AI acceptance remains pending. The saved key's free account and quiet usage were confirmed before setup. Model `openai/gpt-oss-20b` limits: 30 RPM/1,000 RPD/8,000 TPM/200,000 TPD. No key needs to be pasted again.

## One-time AI setup

Skip this section when already configured. For missing readiness, stop API/worker with Ctrl+C and run from the root in normal PowerShell. Current saved account evidence is required:

```powershell
.venv/Scripts/python.exe -m pip install openai-harmony
.venv/Scripts/python.exe scripts/configure_groq_demo.py
```

The optional [official Harmony tokenizer](https://developers.openai.com/cookbook/articles/openai-harmony) verifies the selected model's no-tool framing and demo token estimate. Its installation/download needs networking. Setup uses the existing key for one read-only Groq model lookup, sends no inference, and prints no credentials. It writes `var/groq-readiness.json` and appends the enable flag to `.env` only after verification. Existing credentials/records/ledger stay intact.

The record expires 24 hours after the saved account confirmation. Use this Groq account only through TermsLens during the demo so the ledger accounts for all usage. Provider-specific framing and actual usage still need the live result; official token counts alone do not pass hybrid acceptance.

If setup prints `NOT READY`, use that fixed error to diagnose it. Do not set verification flags manually or overwrite an existing record. The agent's earlier socket restriction is separate from the user's working terminal.

## When AI becomes unavailable

The review-mode hint shows the cause reported by the API. Check the running process from any PowerShell terminal; this makes no Groq call:

```powershell
(Invoke-RestMethod 'http://127.0.0.1:8000/api/health').external_review
```

| Reason / symptom | Action |
|---|---|
| `verification_expired_or_invalid` | An older four-hour record may have expired. From the root run `.venv/Scripts/python.exe scripts/configure_groq_demo.py --renew`, then refresh the browser. |
| `disabled` | Set `TERMSLENS_GROQ_ENABLED=true` in both startup terminals and restart API/worker using the commands below. Process environment overrides `.env`. |
| `readiness_missing_or_invalid` | Use setup above for a missing record; inspect an invalid existing record privately. Do not delete it to force setup. |
| API cannot be reached | Start the API below. If it returns HTTP 503, use `app.manage` to check the database. The worker alone does not serve the website. |
| `configured`, but a saved result is Rules-only | Refresh the page for current availability; old reports retain their original result. Check that report's limitation and the normal worker's settings. Full-policy context may still exceed the AI budget. |

Explicit renewal verifies the model/tokenizer again, archives the previous record, and preserves account identity and quota history. It sends no inference. It refuses expired account confirmation: recheck free access, limits and account usage before updating that evidence; do not merely change its timestamp. Replacing readiness alone is picked up by the API/worker without restarting; restart both if environment settings changed. Refresh the browser in either case.

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
