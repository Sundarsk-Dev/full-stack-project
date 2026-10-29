# TermsLens verification checklist

Check an item only after observing its expected evidence. A passed code check does not close a runtime gate. Keep these IDs stable; record current environment/results in [transfercontext.md](transfercontext.md). The frozen [plan](plan-proposal.md) defines acceptance; [workflow](implementation-workflow.md) defines F1-F8; [debugging guide](architecture-debugging.md) locates failures.

## Code checks completed

- [x] C01 Frozen inputs and document limits: `python scripts/check_context.py`. Expected: all supplied-input hashes unchanged and every maintained document within its cap.
- [x] C02 Python: 66 affected backend checks pass, including five new lifecycle-harness checks after the owner-lock fix. Retain offline migration/earlier baseline evidence. Commands in the handoff/runner; runtime acceptance stays separate.
- [x] C03 Frontend: `npm.cmd --prefix apps/web run build` and `npm.cmd --prefix apps/web test`. TypeScript/Vite build and 18 Node tests pass, including comparison escaping/change labels and deletion confirmation entry. Browser acceptance pending.
- [x] C04 Read-only diagnostics: Python 3.11.3, Alembic 1.13.1, PyMySQL 1.1.1/httpx 0.27.0 installed; both URLs configured. `app.manage --diagnostics` connects to neither DB nor provider. User's authenticated logins now pass (G1-04); Groq readiness remains unavailable.

## Current Gate 2 preflights

- [x] P2-01 Scope and flow: frozen inputs checked; F2 consent/idempotency -> F5 recovery/validation -> F6 atomic publication -> F7 owned report/UI contracts reviewed. No rule/prompt tuning or live calls in this step.
- [x] P2-02 Migration approach and local compatibility: nullable forward migration included in verified head (G1-05); legacy reads, unchanged rules-only hashes, timestamped provider consent, finding provenance and rollback passed local tests. See G2-06a for commands.
- [ ] P2-03 Runtime entry: service/Python install, authenticated DB logins and both migrations pass. Frontend clean install and HTTP/MySQL worker/browser/provider acceptance remain pending (G1-03/06..08, G2-04/07).

## Gate 1: installation and real persisted workflow

Run commands from the project root unless stated otherwise. Use the [README setup](README.md#windows-prerequisites-and-mysql) for local configuration. Keep passwords in local `.env`, never in this checklist.

- [x] G1-01 MySQL service/connectivity: user verified MySQL80 Running and 127.0.0.1:3306 reachable. G1-04 confirms authenticated server/schema access; migration evidence is in G1-05.
- [x] G1-02 Isolated Python install: user completed `.venv/Scripts/python.exe -m pip install -r apps/api/requirements-dev.txt` and `-m pip check` with no broken requirements. Agent verified installed dependencies and ran C02 successfully with the same interpreter. The pip upgrade notice is informational.
- [ ] G1-03 Clean frontend install: `npm.cmd --prefix apps/web ci --cache "$PWD/var/npm-cache"`, then C03. Expected: installation from the checked-in lockfile, build and tests pass. Existing cached/bootstrap dependencies do not establish this.
- [x] G1-04 Authenticated database readiness: on 2026-10-30, user's `../../.venv/Scripts/python.exe -m app.manage` and `--test` both printed PASS: MySQL 8.0.28, respective termslens_db/termslens_test schemas, charset utf8mb4. Earlier 1045 is resolved in the user's terminal. This does not verify migrations or API/worker behavior.
- [x] G1-05 Migrations: on 2026-10-30, user applied both upgrades and ran `app.manage --migrations` and `--test --migrations`. Both printed PASS for MySQL 8.0.28, respective termslens_db/termslens_test schemas, utf8mb4, and applied revision `20261029_lifecycle`. Reproduction commands follow; worker/recovery acceptance remains separate.

```powershell
.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini upgrade head
.venv/Scripts/python.exe -m alembic -c apps/api/alembic.ini -x test=true upgrade head
Push-Location apps/api
../../.venv/Scripts/python.exe -m app.manage --migrations
../../.venv/Scripts/python.exe -m app.manage --test --migrations
Pop-Location
```

- [ ] G1-06 HTTP contracts: `.venv/Scripts/python.exe scripts/check.py --http`. After dependency installation, the agent's focused run timed out at 45 seconds on the first consent/fallback test; no assertion result was observed. Run in the user's working terminal. Expected: eight HTTP/SQLite checks pass for ownership, idempotency, CSRF/logout/expiry, input limits and consent/fallback.
- [ ] G1-07 Real MySQL/API/worker: `.venv/Scripts/python.exe scripts/check.py --http --mysql`. Expected: isolated `termslens_test`, separate worker, two-user access, Unicode evidence and report reopening. Test settings disable Groq. Active unrelated jobs/pending deletions are refused; fixture cleanup preserves other data.
- [ ] G1-08 Browser smoke: set `TERMSLENS_PLAYWRIGHT_MODULE` to existing Playwright and `PLAYWRIGHT_BROWSERS_PATH` if needed; run `node apps/web/tests/browser.smoke.cjs`. Includes URL/consent, versions/profiles, comparisons and deletion confirmation/cancel/status. Syntax passes; launch/assertions remain pending. Mocks do not replace G1-09..12.
- [ ] G1-09 Real browser submission/evidence: user reports registration and request/output working on 2026-10-30; report UI is difficult to understand. This records basic flow, not every acceptance detail. Still verify [gate1.txt](eval/fixtures/gate1.txt) with emoji prefix, saved report, three-group findings, unknowns and exact highlighted evidence. Engine supports eight groups.
- [ ] G1-10 Restart and isolation: note the job/report IDs, stop/restart API and worker, then reopen user A's report. Log in as user B in a separate browser session. Expected: same saved result for A; B cannot read A's jobs/reports/findings/history or attach to A's documents. Record status codes without session tokens.
- [ ] G1-11 MySQL recovery: [prepared suite](comparisons-deletion.md#prepared-mysql-acceptance) checks capture reuse, attempts, single publication and stale fencing after injected interruptions/reconnects. Real execution and actual worker kill/restart remain pending.
- [ ] G1-12 Debug trace and accessibility: trace a submission's `X-Request-ID` through its job and `app.manage --job <actual-uuid>` output to capture/result. Verify a handled API error shows a reference and safe message. Complete login, submit, history and evidence actions with keyboard; inspect focus, readable severity/unknown states and narrow layout. Expected: IDs connect the boundaries and no private content enters diagnostic events.

Gate 1 remains pending until its runtime evidence is recorded. The user authorized Gate 2 code preparation while keeping this verification available for a later review session.

## Gate 2: intelligence

- [x] G2-01 Initial eight-group rules and rubric regression fixtures: C02 includes [eight-groups.txt](eval/fixtures/eight-groups.txt), scope/unknown handling, exceptions and the combined critical cap. These are engineering examples; the reviewed library and measured quality remain pending.
- [x] G2-02 Offline AI boundary: C02 includes consent gating, up to two batches/six clauses each, context preservation/size abstention, strict schema, exact Unicode evidence, duplicates/conflicts and snapshot invalidation. Adapter evidence is separate in G2-05b; this item does not prove runtime hybrid behavior.
- [ ] G2-03 Human data and library: prepare the plan's 100-120 labeled clauses across eight families, heldout >=40 from >=3 families and >=20 adversarial minimal pairs; retain provenance/rights/rationales and prevent near-duplicate leakage before tuning. Expected: people review labels, including novel/exception cases, and the versioned exact library is implemented.
- [x] G2-03a Source tooling: `python -m unittest discover -s scripts/tests -v` passed 11 fake-HTTP tests. WordPress HTTPS capture/hashes verified; CC BY-SA 4.0 observed. [Evaluation guide](eval/README.md) covers rights/labels/split freeze, all pending; 112/42 are target counts.
- [x] G2-03b Offline review: `.venv/Scripts/python.exe -m unittest scripts.tests.test_eval_review -v` passed ten tests for provenance/offsets, duplicates, tampering, Unicode, path/size limits and edit preservation. `scripts/prepare_eval_review.py check var/eval/reviews/wordpress-review-v1.json` passed: 417 candidates, zero labels. Rights, human review, near-duplicates, pairs, freeze/library remain G2-03 work.
- [ ] G2-04 Durable reservations/attempts acceptance: ledger/migration implemented and 15 local fake-provider tests passed; real MySQL locking/recovery remains pending. Run `.venv/Scripts/python.exe scripts/check.py --mysql` after setup (includes `tests.test_ai_mysql`). Expected: unknown quotas mean zero calls; competing jobs cannot overspend; all retries/restarts share two requests, twelve clauses and 10,000/2,000 input/output ceilings; at most one retry. Unknown outcomes retain capacity and are not automatically resent.
- [ ] G2-05 Provider adapter and provenance: bounded Groq transport/readiness and persisted response/usage/provenance are implemented and locally checked (G2-05a/b). Token accounting against the chosen model and real provider behavior remain unverified. Expected: verified accounting plus explicit outcomes for malformed, injected, uncertain, conflicting, 429 and timeout responses without hidden retries/model switches.
- [x] G2-05a Offline coordination: from `apps/api`, `python -m unittest tests.test_ai_review -v`. Observed: 19 fake-transport tests passed for baseline/merge, saved-response recovery, unknown outcomes, two-request retry budget, bounded Retry-After/late replies, abstention, quota failure and stale leases. This does not pass live adapter or UI acceptance.
- [x] G2-05b Local Groq adapter closeout: from `apps/api`, `python -m unittest tests.test_groq -v`. Observed: 29 tests passed for expiring readiness, fake HTTP/usage/caps/429, owned persistence, rules-only zero calls, test CLI isolation under enabled production settings, launch/communication failure, timeout/interrupt reaping and independent child watchdog/cancellation. No network/provider calls; local attestations do not verify free access or model accounting.
- [ ] G2-06 Runtime consent and fallback acceptance: worker/API/UI wiring is implemented and locally checked; HTTP/MySQL/browser interaction and live provider acceptance remain pending. Verify modes, persisted baseline/provenance, owner isolation and visible degradation on the actual stack.
- [x] G2-06a Local integration: from `apps/api`, `python -m unittest tests.test_review_persistence -v` passes ten checks for consent, legacy hashes/reports, owned AI provenance and atomic publication. C03 covers baseline/mode/fallback UI. Direct routes/rendering do not establish HTTP/browser acceptance.
- [ ] G2-06b Browser review: run G1-08's updated smoke for consent/reset, partial-job fallback and baseline switching without new calls. Then verify real history/reopen, AI source/model labels and filters with owned reports. Groq remains disabled here and health makes hybrid unavailable; use only explicitly mocked availability for this smoke.
- [ ] G2-07 Live readiness/smoke: authorized; account/quiet usage and limits confirmed. User reports working request/output after AI setup instructions, but saved effective mode/model/usage/baseline were not provided for inspection. Detailed live acceptance stays open. Five fake-setup tests and four worker CLI tests plus test-Groq isolation passed; no extra provider test requested for this documentation step.

## Later gate exits

- [ ] G3-01 Guarded URL/discovery implementation and tests before arbitrary URLs: all DNS answers, pinned Host/TLS, redirects, ports, byte/deadline limits, robots/domain throttling and crash recovery. Expected: up to four candidates, explicit selection, capture metadata and paste fallback for blocked/dynamic pages. Full requirements remain in the frozen plan.
- [x] G3-01a Network service: from `apps/api`, `.venv` Python with `-m unittest tests.test_url_fetch tests.test_url_runtime -v` passed 32 tests. Covers all-DNS/peer/TLS pinning, rebinding/private redirects, robots/wildcards, same-origin discovery, 8 requests/3 redirects/2-MiB limits, text/heading hashes, host-lease fencing and child timeout/interrupt/communication cleanup. Fake HTTP/DNS and SQLite only; no public calls. See [URL architecture](url-fetching.md).
- [ ] G3-01b Runtime URL acceptance: prepared MySQL suite covers host contention/expiry, snapshots and a synthetic URL worker. Real execution/networking, process crashes and browser discovery/cancellation remain pending.
- [x] G3-01c Local URL integration: from `apps/api`, `../../.venv/Scripts/python.exe -m unittest tests.test_url_jobs -v` passes 15 tests. Covers atomic completion/hash/identity, incomplete files, recovery before/after attachment, stale fencing, extractor/headings/baseline, private reports, unchanged paste hashes, test isolation and discovery auth/user/IP rates. C03 verifies frontend build/rendering/cancellation. No public calls.
- [ ] G4-01 Versions/reuse/comparisons/profiles/deletion: verify owner isolation, concurrent refresh, A -> B -> A history, context/settings invalidation, compatible comparison bases, profile switching without calls and recoverable tombstone/snapshot deletion.
- [x] G4-01a Profiles: ten scoring tests passed. Shared code preserves balanced scores and provides scope-normalized profile weights, caps/bounds/grades from saved evidence. No writes/calls; legacy rubrics remain balanced-only. Runtime acceptance pending.
- [x] G4-01b Versions/reanalysis: from `apps/api`, `../../.venv/Scripts/python.exe -m unittest tests.test_versions -v` passes 13 tests. Covers immutable revisions, compatible reuse, A -> B -> A, context/settings, ownership/consent and recovery. [Contracts](versions.md); real MySQL races/browser acceptance remain pending.
- [x] G4-01c Comparisons/deletion: from `apps/api`, `../../.venv/Scripts/python.exe -m unittest tests.test_comparisons tests.test_deletion -v` passes 17 tests. Covers compatible bases, Unicode/reopen, exact/bounded alignment, text/finding changes, active jobs/tombstones, cursor/file/DB recovery, stale-write fencing, surviving reports and quota preservation. [Contracts](comparisons-deletion.md); C03 covers UI rendering.
- [ ] G4-01d Runtime lifecycle: eight [prepared MySQL checks](comparisons-deletion.md#prepared-mysql-acceptance) cover refresh/compare/publish/delete ordering, recovery, quotas and isolated child worker. Unrun; browser compare/confirm/cancel/status and reopening remain pending.
- [x] G4-01e Offline harness: from `apps/api`, `../../.venv/Scripts/python.exe -m unittest tests.test_lifecycle_harness -v` passes five checks for recovery, scoped cleanup/idle guards, disabled-Groq worker logic and opt-in gating. SQLite/mocked locks do not establish MySQL acceptance.
- [ ] G5-01 Evaluate heldout data in both modes: report raw counts, high-severity precision/recall, macro F1, abstention/coverage and targets/shortfalls. Mocked or engineering fixtures cannot establish measured hybrid value.
- [ ] G5-02 Benchmark the fixed 10,000-word fixture: warm-up plus 20 runs, median/p95; separate processing, retrieval, queue, network and provider times. Compare the plan's <=5-second processing and <=1-second retrieval p95 targets.
- [ ] G5-03 Final full-stack/demo/report: user says output UI is hard to understand; improve summary/evidence hierarchy and controls, then verify keyboard/browser behavior. README now leads with startup/output/Git instructions. Remaining runtime checks, screenshots, evaluation and course report are pending; preserve supplied inputs.

Record runnable commands and actual results; keep environment status in the single handoff.
