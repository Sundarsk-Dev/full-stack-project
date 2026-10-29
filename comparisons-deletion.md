# Saved comparisons and recoverable deletion

Gate 4 implementation, tested locally with SQLite, synthetic policies and private temporary files. MySQL locking/migrations and real browser acceptance remain open; see the [checklist](verification-checklist.md).

## User flow and API

Open a report, select another saved report of the same policy type, choose balanced or privacy-first and select **Compare reports**. Earlier revisions of the same document produce a version comparison; distinct documents produce a policy comparison. Saved comparisons reopen from either report, with paginated passages and a changed-only filter.

All routes require an authenticated owner. Mutations enforce origin/CSRF and rate limits. Comparisons perform no fetch, provider call or report mutation.

| Route under `/api` | Contract |
|---|---|
| `POST /comparisons` | `{left_id, right_id, kind, profile}`; canonical report UUIDs, `kind` is `versions` or `policies`, profile defaults to `balanced`. Returns 201 with `comparison_id`. |
| `GET /comparisons?analysis_id=...` | Owned history; limit 1-50, offset 0-10000. Hides comparisons involving tombstoned documents. |
| `GET /comparisons/{id}` | Saved scores/summary and exact passages; limit 1-100, offset 0-10000, optional `changes_only=true`. |
| `DELETE /documents/{id}` | Returns 202 with durable deletion receipt. Rejects queued/running jobs with 409. Repeated owned requests return the same receipt. |
| `GET /deletions` | Owned receipt history; limit 1-50, offset 0-10000. Receipt exposes IDs, status/times and fixed error code, never title/body. |

**Delete all versions of this document** opens confirmation and focuses Cancel. Confirming hides the document immediately. The running worker performs cleanup; the UI polls pending receipts. A receipt survives restart and final document removal. Other documents remain available; comparisons that reference the deleted document disappear.

## Comparison basis and alignment

Both reports must have the same document type/applicable groups, complete engine identity, effective review mode and current scoring-profile revision. Hybrid reports also require matching provider/model/prompt provenance. Incomplete or incompatible settings return 409 with instructions to reanalyze using common settings and fresh consent. Requested mode and any degradation are displayed for both sides.

Creation locks the owner, then documents in deterministic order, and rechecks tombstones before saving. Scores come from stored profile views. Comparisons store passage references, summary, basis and diff revision `exact-token-dice-v1`; reads reconstruct immutable evidence. Source values render as escaped text.

Exact text matches align first, consuming duplicates one-to-one. Unmatched passages use tentative token Dice similarity, threshold 0.6, at most 4,096 pairs and the first 2,048 characters/128 tokens per passage. Deterministic greedy matching keeps every passage once. A visible limitation flag discloses an exhausted pair budget; sampled similarity never establishes equivalent meaning.

Text changes (added/removed/modified) are separate from changed category/severity findings or unrated status. Explanation wording alone is not a change. Original full-text inequality is recorded separately, including order/whitespace differences that exact passage matching can leave unchanged. Unaligned passages have no interpretation comparison.

## Cleanup and crash recovery

1. The API locks owner then document, rejects active jobs, commits the tombstone and a pending `document_deletions` receipt before returning. Existing reads/attachments/refresh/reanalysis reject tombstones.
2. The single worker processes a due receipt before each job claim. It verifies ownership and every stored snapshot reference against the document's jobs, then selects up to 50 job folders after the saved cursor.
3. File cleanup occurs outside DB transactions. Each job's OS file lock serializes cleanup against snapshot saves. Cleanup persists a minimal deletion barrier before removing completed envelopes and interrupted temporary files. Barriers prevent stale workers recreating files.
4. Cursor advances only after successful file cleanup. When all folders are handled, one transaction removes dependent comparisons, findings, clauses, category scores, AI payloads, jobs, reports, versions and document; self-referential retry/parent links are cleared first. Receipt becomes complete.
5. Filesystem/database failures retain the pending receipt with `cleanup_pending` and a ten-second retry delay. Removed files need not be restored to retry a rolled-back transaction. Account-wide quota windows and consumed/reserved usage are never refunded by deletion.

Snapshot cleanup accepts only validated flat job artifacts under the configured private root; it refuses symlinks/reparse points, foreign paths and unexpected directories. It never recursively removes unknown material. Tiny `.locks/<owner>/<job>` and `.deleted/<owner>/<job>` files intentionally remain as concurrency barriers; they contain no policy text, URL or provider payload. Existing unrelated archives/caches are preserved. Failed jobs never attached to a document are not swept by document deletion.

Production uses `var/snapshots`; `app.worker --test` uses only `termslens_test` and `var/tests/snapshots`, with Groq settings cleared. `--once` processes one cleanup batch and at most one analysis job; continue the normal worker to finish larger deletions. No external calls are required for comparisons or cleanup.

## Migration and verification

Head/`SCHEMA_REVISION` is `20261029_lifecycle`, following `20261029_versions`. It adds `comparisons` and `document_deletions` without altering saved reports. Offline SQL generation passes; both real schema upgrades remain unapplied. Follow the [README migration block](README.md#windows-prerequisites-and-mysql) during final runtime verification.

```powershell
Push-Location apps/api
../../.venv/Scripts/python.exe -m unittest tests.test_comparisons tests.test_deletion -v
Pop-Location
.venv/Scripts/python.exe -m unittest scripts.tests.test_migrations -v
npm.cmd --prefix apps/web test
npm.cmd --prefix apps/web run build
```

Observed: 17 comparison/deletion tests within 66 affected backend checks, plus one offline migration regression; frontend build and 18 Node tests pass. Coverage includes exact Unicode evidence/reopen, incompatible bases, duplicates/bounds, owner/tombstone guards, active-job rejection, filesystem/DB failure recovery, batching, stale-writer barriers, surviving-report integrity and unchanged quota usage. Browser smoke now covers compare/confirm/cancel/status but has only passed syntax checking on this host.

Runtime evidence remains open. The prepared MySQL checks below cover locks and injected interruptions; actual worker termination/restart, browser keyboard, confirmation and reopening still need observation.

## Prepared MySQL acceptance

`tests.test_lifecycle_mysql` adds eight opt-in checks. **Written, not run against MySQL.** They use real test-schema transactions when enabled, with synthetic policy captures and no public/provider calls.

| Check | Required evidence when executed |
|---|---|
| Compare/delete in both orders | Comparison first saves then becomes unreadable after tombstone; deletion first rejects comparison. Surviving report remains unchanged. |
| Enqueue/delete in both orders | Enqueue first makes deletion reject the active job; deletion first makes refresh reject the tombstone. |
| Publish/delete in both orders | Publication first lets deletion proceed; deletion first rejects the active job, allowing publication. Neither path reverses owner/document lock order. |
| Concurrent capture attachment | Two overlapping refresh attachments share one changed version; reverting produces A -> B -> A with ordered parent links. |
| URL host contention | Competing inserts/acquisitions admit one holder. Expired lease can be replaced; stale release cannot clear the replacement or lower its interval. |
| Snapshot/capture recovery | Interruption after file completion recovers without refetch; later capture interruption preserves the version, attempt count and one report. Stale publication fails. |
| Cleanup recovery | Interrupt after files and before final DB commit; reconnect/retry completes the receipt, preserves surviving reports/quotas and rejects stale snapshot writes. |
| Separate test worker | A bounded child runs `--test --once`, cleans a receipt then publishes a synthetic URL job. Enabled synthetic production Groq settings are cleared; only the test schema/snapshot root is selected. |

Transaction-order checks coordinate SQL execution with events at actual `FOR UPDATE` statements. The first lock is observed before starting its contender. Waits and InnoDB lock waits are bounded. Comparison/report insertion now locks the owner before documents, matching enqueue/deletion and avoiding the reversed lock order caused by MySQL owner foreign-key checks. This fix follows code review; real MySQL proof is pending. Recovery faults inject uncaught interruptions and reconnect, without claiming an observed OS process kill. URL fetches are injected.

The fixture requires `termslens_test` at `20261029_lifecycle`, takes the test worker advisory lock and refuses existing active jobs or pending deletions. Stop other test workers/acceptance runs first. It creates UUID-owned fixture records and synthetic host rows, cleans only those IDs/files, and leaves unrelated data and quota windows intact. The separate child gets credentials through its private environment, never argv/output, and uses a hidden Windows process with a 25-second timeout. Parent cleanup resumes only after it exits. Production configuration files are unchanged.

The existing HTTP/MySQL worker check now also refuses pending deletions before launching its worker. `scripts/check.py --mysql` includes all three MySQL suites; the new lifecycle suite has a separate 60-second process bound.

Run only during the deferred database verification pass, after both login/migration checks. For this suite alone, from the project root:

```powershell
$previousMySQLTestOptIn = $env:TERMSLENS_RUN_MYSQL_TESTS
Push-Location apps/api
try {
    $env:TERMSLENS_RUN_MYSQL_TESTS = '1'
    ../../.venv/Scripts/python.exe -m unittest tests.test_lifecycle_mysql -v
} finally {
    if ($null -eq $previousMySQLTestOptIn) { Remove-Item Env:TERMSLENS_RUN_MYSQL_TESTS -ErrorAction SilentlyContinue }
    else { $env:TERMSLENS_RUN_MYSQL_TESTS = $previousMySQLTestOptIn }
    Pop-Location
}
```

Observed offline only: `../../.venv/Scripts/python.exe -m unittest tests.test_lifecycle_harness -v` from `apps/api` passes five checks. Shared recovery scenarios run on isolated SQLite; the child harness exercises real worker logic with a mocked advisory lock and test database factory. Fixture cleanup/idle guards preserve another owner's report. A subprocess verifies all eight MySQL checks skip without reading configuration or connecting when not opted in. This does not pass any MySQL acceptance item. Default `scripts/check.py` includes these five local checks.
