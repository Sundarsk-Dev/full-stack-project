# Versions, refresh and reanalysis

Gate 4 implements immutable version/report history and compatible reuse, with [comparisons/diffs and recoverable deletion](comparisons-deletion.md). Local tests do not establish MySQL concurrency or browser acceptance.

## User flow

Open a saved report and use **Versions and reanalysis**. Select a version to list its report revisions, or open its latest report. History is paginated; selecting a version creates no work.

- **Check URL for changes** fetches the document's original saved URL through the existing guarded service.
- **Replace pasted text** starts with the displayed report's text. Review it before replacing the current document; an older report can intentionally restore earlier wording.
- **Reanalyze saved version** uses that immutable capture without fetching. It always creates a new report revision with current rules and the selected review mode.

Changing action, version, replacement text or mode resets external-review consent. Groq remains unavailable until readiness is established. Rules-only stays local. Progress reports the source check time, whether content changed, and whether an existing report was reused.

## API contracts

All routes require an owned, non-tombstoned document. Mutations use session/origin/CSRF checks, shared user/IP submission limits, a five-active-job owner cap and an idempotency key. Same key/input returns the same job; different input returns 409. Source lookup and enqueue lock owner then document. Jobs commit before 202.

| Route under `/api` | Request / result |
|---|---|
| `GET /documents/{id}/versions` | `limit` 1-50 and `offset` 0-10000; ordered capture metadata, count/latest report per capture and most recent successful source check. No policy bodies. |
| `GET /documents/{id}/versions/{version}/analyses` | Same pagination; immutable report revision IDs, dates and requested/effective modes. |
| `POST /documents/{id}/refresh` | `{ "mode": "rules_only" }` for URL documents; add replacement `text` for pasted documents. Type/source URL derive from owned storage. |
| `POST /analyses` | Supply exactly one of `text`, `url`, or owned `version_id`, plus `document_type` and mode. Saved-capture reanalysis retains the document's type. |
| `GET /jobs/{id}` | Adds operation, document/version IDs, `checked_at`, `capture_changed` and `result_reused`. |

Hybrid mutations additionally require strict boolean `consent: true` and `provider: "groq"`; the API timestamps fresh consent. Existing paste/URL submission hashes remain unchanged. Discovery remains independent and never creates jobs.

## Persistence and reuse

`worker.capture()` fetches/writes files outside row transactions, then reacquires the job lease and document row lock. Under that lock it compares against the latest committed capture and allocates the next version number/parent. Exact cleaned text, headings and extractor must match for capture reuse. Hash equality alone is insufficient. A -> B -> A creates three versions; new definitions or heading context trigger full local analysis.

Capture attachment and source-check metadata commit together. A recovered job with an attached version uses it again; a crash before attachment recovers its completed private snapshot. Original capture timestamps/text remain immutable. Unchanged URL checks preserve retrieval time and URL/body-hash metadata on the job. Document deletion removes these job-owned envelopes along with version snapshots.

An unchanged refresh considers the latest report revision for that version. Reuse requires the same owner/document/version, current engine and complete rules baseline/context, requested/effective mode and profile revision. Hybrid reuse additionally requires fresh consent and matching Groq model/prompt provenance. Degraded results or missing legacy metadata cannot be reused as successful hybrid results. A job with existing provider attempts must finish its own saved work.

Compatible reuse links the existing report and completes the job atomically, without new AI calls, quota mutation or report rows. Limited hybrid reports retain their partial state. Explicit reanalysis bypasses reuse and allocates the next report revision under the document lock. Old reports/provenance stay unchanged.

## Migration and checks

Migration `20261029_versions` follows `20261029_capture`. It adds job operation/document/check/reuse metadata, backfills document IDs and report revision numbers, adds uniqueness per version/revision and nullable profile identity. A nonunique FK-supporting result index permits report reuse; downgrade refuses duplicate links. Current head `20261029_lifecycle` adds comparisons and deletion receipts.

Real migrations remain unapplied. The README commands migrate both isolated schemas during the deferred database verification pass. Offline generation makes no connection and reads no credentials:

```powershell
.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade 20261029_capture:head --sql
Push-Location apps/api
../../.venv/Scripts/python.exe -m unittest tests.test_versions -v
Pop-Location
```

Observed: 13 new version tests plus affected worker/URL/AI/diagnostic and offline migration checks pass. Tests cover reuse without calls, explicit revisions, model/profile/engine changes, A -> B -> A, context invalidation, queued refresh ordering, recovery, ownership/tombstones, strict consent and URL check timestamps. SQLite ordering tests do not prove MySQL locks. Frontend build/16 Node tests pass; the updated browser smoke remains unexecuted on this restricted host.
