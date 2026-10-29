# TermsLens: Final Implementation Plan

Updated: 2026-10-26. Status: ready for implementation approval; no app built.
[TermsLens_Project_Plan.md](TermsLens_Project_Plan.md) remains the unchanged source brief. This replaces the previous proposal; the PDF remains a report-format reference.

## Fixed outcome and scope

Answer: "What does this policy allow? Where is the evidence? What changed?" Build a full-stack app with private history, evidence-backed findings and meaningful comparisons. Use reviewed rules for supported cases and bounded AI interpretation for unfamiliar/ambiguous clauses.

**One English policy per job.** Accept pasted text or a static public policy URL. Homepage discovery offers up to four legal links; the user selects one. No multi-document job aggregation or whole-site grade.

| Required | Demonstration |
|---|---|
| Input/progress | URL/paste, declared type, mode/consent, actual pipeline stages. |
| Hybrid analysis | Rules/exact matches first; contextual AI review of unresolved substantive clauses. |
| Report/evidence | Findings/explanations, exact highlighted quotes, source labels, coverage and score bounds. |
| Accounts/history | Register/login/logout, private reports, restart/reopen, owned deletion. |
| Versions | Manual URL refresh/replacement paste, changed passages and supported concern changes. |
| Comparison/profiles | Two same-type policies under common settings; balanced/privacy-first views. |
| Delivery | Migrations, fixtures, evaluation, focused tests, accessible demo and final report. |

Eight groups: 1 sharing/selling; 2 retention/deletion; 3 tracking/consent; 4 content licensing/AI training; 5 renewal/refunds/cancellation; 6 unilateral changes; 7 arbitration/class waivers; 8 termination/export. Support specific patterns and AI interpretations, without claiming every legal formulation.

Defer monitoring/email, sharing, admin/learning, embeddings/RAG, extensions/export, multilingual/compliance, headless scraping and deployment automation.

## Responsibility boundaries

| Work | Owner |
|---|---|
| Fetching, segmentation, storage, cache and text diff | Application code. |
| Supported contextual patterns/exact matches | Reviewed versioned rules/library, evidence and templates. |
| Unfamiliar wording, ambiguity, nuanced exceptions | AI with the clause and relevant context. |
| Validation, score formula/caps, profiles/comparisons | Code; accepted AI labels can feed assisted scores. |
| Scope/rubric/seed labels/acceptance/provider consent | People. |

AI has no tools, rule/weight/library writes, overall-grade output or provider escalation. Rules-only supports privacy/offline use; evaluated hybrid operation is still required.

## Stack and readiness

React/Vite/TypeScript; FastAPI/Pydantic; SQLAlchemy/Alembic; MySQL 8; one separate Python worker with a durable MySQL jobs queue. Private local snapshots, same-origin serving, Vite development proxy and job polling. No Redis/vector/object-storage service.

Use apps/web, apps/api, eval and private var/snapshots; isolated pinned dependencies and accessible category bars.

Known: Python 3.11.3, Node 22.23.4/npm 10.9.9, MySQL client 8.0.28. Port 3306 was unreachable on 2026-10-26. Prove server/login/migrations in termslens_db/termslens_test first; no configuration changes during planning.

## Pipeline and evidence

1. Authenticate; validate input/type/mode/consent. Save an owner-scoped idempotent job; return HTTP 202/job ID. Homepage discovery is a separate candidate-list request.
2. Guardedly fetch the selected policy or accept bounded text. Save immutable cleaned text/headings, capture time/hash/extractor version and private raw snapshot when fetched.
3. Segment sections/paragraphs, retaining definitions, actors, negation and exceptions as context. Exclude only clearly non-substantive navigation/contact fragments, with reasons.
4. Apply reviewed exact matches and contextual rules. Require actor/action/qualifier support, not keyword presence. Preserve numbers/negation during normalization; conflicting/unsupported readings abstain.
5. Prioritize unresolved substantive clauses for AI. Keywords prioritize candidates but cannot neutralize novel wording; supported clauses need no AI call.
6. Validate findings, preserve attribution and mark uncertain/rejected/budget-excluded clauses unrated. Code computes scores/template summary and publishes the report atomically.
7. Refresh compares against the latest cleaned capture. Identical text reuses compatible analysis and records the check on the job; changed/reverted text creates a new ordered version.

A clause can support multiple categories. Deduplicate issues; favorable repetition cannot dilute concerns. Evidence offsets refer to stored Unicode code points; convert correctly for JavaScript UTF-16 or render server-provided slices. Test non-BMP characters.

## Required bounded AI

Use a small Groq classify-batch adapter plus no-call fallback, one configured model, no embeddings and no routine AI summary. Verify current free model/account readiness before live integration.

- Explicit hybrid consent permits sending selected clauses and context to Groq. Rules-only sends nothing. Record consent/provider and display requested/effective mode.
- **Two requests, twelve clauses maximum per analysis**, six per batch. All attempts/retries share the two-request ceiling; at most one retry. Total ceilings: 10,000 input/2,000 output tokens; stricter account/model/day limits prevail.
- Include IDs, heading, nearby passages and relevant definitions. If adequate context cannot fit without cutting an exception, abstain. Policy text is untrusted data; model has no tools.
- Output: clause ID/category/rating/severity, quote, explanation and explicit uncertainty. Check schema/IDs/enums/severity consistency, exact evidence and supported-rule contradictions. Quotes establish provenance, not correct interpretation.
- Accepted AI findings contribute to a labeled hybrid report; retain rules-only findings as baseline. Self-reported confidence is not measured accuracy. Uncertainty/conflicts abstain; no automatic learning.
- Atomically reserve request/token capacity across active quota windows before sending; record/reconcile every attempt conservatively. Unknown limits mean zero calls. Bound timeouts/Retry-After; no hidden repair loop/model escalation.
- Provider/key/quota failure yields a visibly degraded rules-only report with unresolved counts. Persist responses/reservations for recovery; do not automatically replay completed/interrupted calls or reset budgets. Degradation is not hybrid acceptance.

Verify current Groq no-billing terms, settings, model and quotas; keep keys in local environment storage. No provider calls/accounts/purchases are authorized by this planning turn. If free access cannot be established, record the hybrid acceptance blocker; no automatic paid/local substitute.

## Rubric and coverage

Selected consumer impacts, not legality. Favorable=0, neutral=1, concerning=2-3, invasive=4-5; unrated has no severity. Category risk is maximum accepted severity, with unknown flags/evidence. Balanced weights=1/8 each; privacy-first=(.25,.20,.20,.15,.05,.05,.05,.05).

Fixed applicable groups: Terms=all eight; Privacy=1,2,3,4,6; Cookies=1,2,3,6; Refund=5,6. These are project scope rules. Normalize profiles over the fixed applicable set, never around missing evidence. Out-of-scope concerns remain visible and withhold grading until a suitable type is used.

Observed risk is the category maximum, or 0 without rated evidence. Unknown means unresolved relevant or insufficient category evidence; uncertain relevance affects possibly relevant groups. Best score=100-20*sum(weight*observed risk); worst substitutes 5 for unknown groups. "Not found here" is uncertainty, not proven absence.

Cap both bounds at 74 for accepted explicit data sale; 59 for accepted perpetual licensing plus mandatory arbitration. Attribute AI evidence supporting caps. Bands: A>=90, B>=75, C>=60, D>=40, F<40. One grade needs both bounds in one band, >=80% substantive-clause coverage and consistent scope; otherwise show interval/insufficient evidence.

Coverage=rated substantive clauses/all substantive clauses; multiple findings count once. Show category unknowns and rules-only/hybrid coverage. Profiles recalculate from stored evidence without another fetch/model call.

## Versions, comparisons and reuse

Version comparisons require one logical document; cross-policy comparisons require matching type/applicable groups. Require common extractor/segmenter/rules/library/rubric, effective mode, hybrid model/prompt and profile revision. Require reanalysis of incompatible captures with valid consent.

Align exact passages, then bounded text similarity for unmatched sections; similarity never approves meaning. Rerun cheap rules across all context because changed definitions affect untouched passages. Show actual text changes separately from settings/model interpretation changes; changed AI wording alone is not a policy-change event.

Cache identity: owner/document/version/context/engine/library/rubric/mode; hybrid model/prompt/rerun revision; view profile revision. No cross-user reuse. Saved findings stay immutable; reruns create revisions.

## Database and API

| Tables | Responsibility |
|---|---|
| users; sessions | Username/Argon2id hash; hashed opaque session token/expiry/invalidation. No email/SSO. |
| documents; document_versions | Owner/type/source; ordered immutable captures, parent/text/hash/metadata/snapshot. |
| analyses; clauses; clause_findings | Engine/effective mode/provenance; contextual spans; rule/AI findings/evidence/abstentions. |
| category_scores | Maxima/unknowns/counts; profile weights calculated on read. |
| jobs | Owner/input/idempotency/consent, one result FK, state/stage/lease/attempt/error/check time. |
| comparisons | Owner, two analyses, kind/common settings/profile and passage diff. |
| ai_calls; ai_budget_windows | Attempts/responses/usage; transaction-locked quota reservations. |

Rules/taxonomy/templates/exact library are versioned source files, not an admin subsystem.

| Route under /api | Purpose |
|---|---|
| POST /auth/register, /auth/login, /auth/logout; GET /me | Local account/session lifecycle. |
| POST /discovery | Guarded homepage; up to four typed candidate URLs and limitations. |
| POST /analyses; GET /jobs/{id} | URL/paste/owned-capture reanalysis, mode/consent/idempotency; poll one result. |
| GET /analyses; GET /analyses/{id}; GET /analyses/{id}/findings | Paginated private history, profile report, bounded filters. |
| GET /documents/{id}/versions; POST /documents/{id}/refresh | Owned version history and URL recheck/replacement paste. |
| POST /comparisons; GET /comparisons/{id} | Compatible owned reports under common settings. |
| DELETE /documents/{id}; GET /health | Owned data/snapshot deletion; API/database readiness. |

Serialize refresh with a document row lock. Same owner/key/input returns the same job; changed input with that key returns 409. Jobs end done/partial/failed and report effective mode. Ownership covers every read/write, including worker attachment.

## Operational boundaries

- Fetch public HTTP(S), ports 80/443 only. Reject credentials/private/reserved/loopback/link-local IPv4/IPv6. Validate all DNS answers, pin Host/TLS-correct connections and revalidate redirects. A DNS precheck alone is insufficient.
- Per discovery/fetch operation: eight HTTP requests including robots/redirects, three redirect hops, 2 MiB decompressed/response, 25,000 words, 60-second network deadline. Same-origin discovery, robots/domain throttling; paste for dynamic/blocked pages. Bound parsing/regex/diff work.
- One worker/one active job initially. Transactional claims, expiring leases/heartbeats, three total job attempts; recover without duplicate versions/results. AI has separate bounded timeouts and persisted attempts preventing automatic replay.
- HttpOnly/SameSite cookies, Secure with HTTPS, expiry/logout, origin/CSRF checks, bounded login/submission/discovery rates. Escape text; never render snapshots or log bodies/passwords/keys.
- Deletion rejects active jobs. Tombstone transactionally, block refresh/reanalysis/attachments, remove comparisons/derived records and retry snapshot cleanup before finalizing. Recovery completes interrupted cleanup.
- Keyboard-operable evidence/filters, textual severity, visible focus and clear unknown/error/mode states. Show scope/capture date and "Automated analysis, not legal advice."

## Performance and Radnus HPC

Radnus HPC is installed but disabled in C:/Users/KUZHALAMANI/.codex/config.toml, lines 98-100, and absent from the session catalog. Registry scope: assembly/SIMD/CUDA hotspots after enablement. Instructions were not loaded; configuration unchanged. Eight logical CPUs visible; privileged CPU/RAM/service inventory inaccessible. No application benchmark exists.

First compile rules once, segment/normalize once, index owner/history/job/signature queries, batch writes, avoid N+1 reads, reuse analyses and limit AI batches. Separate queue/network/provider time; GPU work cannot speed remote waits.

Benchmark a fixed 10,000-word fixture: warm-up plus 20 runs, median/p95 for parsing/rules/persistence/retrieval, queue and AI separately. Targets: warm uncached rules-only processing p95 <=5 seconds, saved retrieval p95 <=1 second; exclude startup/network/AI from processing and report them separately.

Only propose native/SIMD/GPU if profiling still shows a dominant CPU hotspot after simpler fixes. Require portable reference, unchanged outputs and before/after measurements. Radnus activation/native dependencies require a separate scoped approval; unnecessary to start.

## Delivery gates and acceptance

| Gate | Deliver and verify |
|---|---|
| 1. Persisted slice | Real MySQL/migrations, two users; login -> queued paste -> three-group rules report -> Unicode evidence -> restart/reopen. Checkpoint only. |
| 2. Intelligence | All eight groups/rubric/library; Groq consent/batching/reservations/validation/degradation, labeled novel/exception cases; verified free access before live calls. |
| 3. URL workflow | Guarded fetch/discovery, metadata/progress; URL security and worker crash tests before arbitrary public URLs. |
| 4. Novelty | Private history/deletion, manual versions/diffs, comparisons/profiles/cache reuse. |
| 5. Finish | Evaluate both modes, benchmark, end-to-end/build, visual refinement/affected checks; actual screenshots/course report last. |

Use 100-120 human-labeled clauses from eight policy/fixture families with rationale/provenance/rights. Hold out >=40 clauses from >=3 families; exclude near-duplicates from rules/library/prompt tuning. Add >=20 adversarial minimal pairs for actors/negation/exceptions/definitions/instructions. People resolve labels; AI is not ground truth.

Report raw counts, high-severity precision/recall, macro F1, abstention/coverage in both modes. Unmeasured hybrid targets: precision >=90%, recall >=70%, coverage >=60%; seek >=10 percentage points coverage gain over rules-only while maintaining precision. Revise bounded review or disclose shortfalls; mocked/offline operation alone does not prove intelligence.

Check rules/bounds/caps, Unicode quotes, AI schema/evidence/injection/uncertainty/budgets/failure, sessions/ownership, URL DNS/redirect/size limits, real MySQL API/worker/restart/deletion, version/context/cache invalidation, comparisons, frontend build and keyboard/browser workflow. Fake providers/network test failures; separately authorized live hybrid evaluation proves value.

Demo: hybrid rule/AI findings and uncertainty; compare B; switch profiles without AI; refresh/diff/reuse; restart/history; deny user B access. Show URL discovery/fetch, with labeled fixtures if unavailable.

Done: scoped features, checks/evaluation, persistence, degraded mode and evaluated AI. Missing free access stays pending. Deadline/team/rubric are unknown; confirm mandatory stack constraints at setup.

README will supply exact Windows setup/run/evaluation/benchmark commands and environment variables, without secrets. Commands do not exist yet. Implementation requires approval under the user's working agreements; this turn updates plan/handoff only.
