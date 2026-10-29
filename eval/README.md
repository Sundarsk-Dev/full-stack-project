# Evaluation source preparation

The user selected online policy examples. [source-candidates.json](source-candidates.json) proposes eight policy families: Automattic, Discourse, Mozilla, Creative Commons, EFF, Wikimedia, OpenStreetMap and Blender. WordPress terms were captured from the user's terminal and their hashes verified. Other sources remain candidates. The [handoff](../transfercontext.md) records acquisition evidence; content/rights review, human labels and live-provider results remain pending.

The candidate allocation is 14 base clauses per family: 112 total, with 42 from three heldout families. It is a sampling proposal, not a completed split or label count. Different sites, mirrored policies and versions from the same organization belong to one family. Replace unavailable or unsuitable candidates before freezing the split; do not move difficult heldout examples into development later.

## Collect public evidence

From the project root, list candidates without connecting:

```powershell
python scripts/collect_eval_sources.py
```

On a host where public HTTPS works, collect one candidate first. Only after that succeeds should you collect the remaining named IDs; `all` is available for the initial complete acquisition:

```powershell
python scripts/collect_eval_sources.py --fetch wordpress-terms
python scripts/collect_eval_sources.py --fetch discourse-terms mozilla-terms cc-terms eff-privacy wikimedia-terms osm-privacy blender-privacy
python scripts/collect_eval_sources.py --fetch groq-models groq-rate-limits groq-pricing
```

The collector accepts catalog IDs only and checks fixed HTTPS hosts. Each source gets one GET, no redirects/proxies/authentication/retries, a 2-MiB body cap and a parent-enforced 15-second total deadline. It stops at the first failure. Redirects, bot challenges and inaccessible pages require investigation; do not label a challenge page as policy content or automatically substitute a mirror.

Successful acquisitions create a new private `var/eval/sources/<source-id>/<capture-id>/` containing `raw.bin`, `text.txt` and `capture.json`. Metadata records the URL, UTC acquisition time, hashes, family, proposed split and extractor version. Existing captures are preserved. Only a folder with a final `capture.json` is complete; incomplete folders are not evidence.

`text.txt` is a research transcription, retaining navigation and footer text for inspection. Verify headings, definitions, exceptions and the page's effective date against the source. This helper does not implement the application's Gate 3 URL workflow or certify its network protections. Do not render raw HTML in the app.

Read the page's actual copyright/license notice and applicable reuse terms. Record the permission, attribution and adaptation conditions with a dated source reference before including text or derived pairs in the corpus. The collector leaves rights/content reviews pending even when a download succeeds; familiar organization names do not establish permission.

## Prepare a private review worksheet

The offline helper uses existing captures only; it does not load `.env`, connect, predict labels or change application rules. The WordPress draft is already at `var/eval/reviews/wordpress-review-v1.json`. To reproduce under a new output name:

```powershell
.venv/Scripts/python.exe scripts/prepare_eval_review.py prepare var/eval/sources/wordpress-terms/8486343d2107439f860e17420a50dfd1 --output var/eval/reviews/wordpress-review-v2.json
.venv/Scripts/python.exe scripts/prepare_eval_review.py check var/eval/reviews/wordpress-review-v1.json
```

`prepare` accepts multiple explicit capture folders, preserves the sources and refuses to overwrite an existing worksheet. Both commands verify the catalog identity/split, raw/text hashes and Unicode offsets. Sources must be complete policy captures; provider reference pages and paths outside the capture area are rejected. Worksheet files stay under private `var/eval/reviews` and are capped at 32 MiB/2,000 candidates.

Each nonempty source line becomes a candidate, including navigation/headings. The current WordPress draft has 417 candidates, zero selected and no labels. It retains exact text and adjacent-line context; this is not automatic clause selection. Use the full capture to review definitions, distant exceptions and cross-references before selecting any candidate. Adjacent lines alone may be insufficient.

People fill `sources[<capture>].review` with content/rights review, date, license reference and attribution. Set suitable records' `selected` to `true` and fill `human_review` using the fields below; keep decisions blank until a person reviews them. Source metadata, excerpt offsets/quotes and IDs are integrity fields. Preserve the source files when making a new draft or reviewing a different span.

`check` reports candidate counts and flags selected exact duplicates across development/heldout splits after Unicode normalization, case folding and whitespace normalization. Family/split edits that disagree with the captured catalog are rejected. Exit 0 establishes only these checks; cross-split duplicates or integrity failures exit 1. The tool always reports `human_labels_verified: false` and `corpus_ready: false`: it does not adjudicate labels/rights, screen semantic near-duplicates, validate adversarial pairs, import a library or freeze a corpus. Run it again after worksheet edits.

## Human annotation and split freeze

Use the [frozen plan](../plan-proposal.md#delivery-gates-and-acceptance) and its [rubric](../plan-proposal.md#rubric-and-coverage). People choose and resolve labels. Preserve actual source wording and enough surrounding context to interpret it; AI predictions must not fill the ground-truth fields.

For each selected clause, retain these fields in the private review worksheet:

| Field | Required content |
|---|---|
| Identity | Stable clause ID, family ID, capture path and text SHA-256, document type, proposed development/heldout split. |
| Evidence | Exact quote and Unicode code-point start/end offsets into the immutable `text.txt`. Retain headings, definitions, neighboring exceptions and relevant cross-references. |
| Provenance/rights | Official source URL, source effective date if stated, acquisition time, license/permission reference and required attribution. |
| Human decision | Substantive status; applicable category/rating/severity findings, or explicit uncertainty/abstention. Multiple categories are allowed. Missing evidence is not safety. |
| Rationale | Explain actor, scope, duration, numbers, conditions and exceptions supporting each decision. |
| Review | Human reviewer identifier/date, unresolved disagreements and final adjudication. Keep blank until reviewed. |
| Pairing | Base or adversarial record; pair ID, changed dimension and relation to the original source, including adaptation permission. |

Before any rule/library/prompt tuning:

1. Verify 100-120 reviewed base clauses from eight families and at least 40 heldout base clauses from at least three families. Cover all eight concern groups and unfamiliar/exception cases; inspect the final mix rather than treating source count as category coverage.
2. Keep entire families, mirrored text and all variants of a minimal pair in one split. Compare exact normalized text and near-duplicates across splits and against existing rules, prompts, tests and `eval/fixtures`. Use lexical screening plus human review for semantic duplicates; neither similarity nor matching quotes proves interpretation.
3. Add at least 20 human-reviewed minimal pairs, separately counted from the base sample. Aim for four each on actors, negation, exceptions, definitions and injected instructions. Record the expected label change or invariance and rationale; adaptations require appropriate rights.
4. Freeze source hashes, clause/context hashes, IDs, family allocation and reviewed labels in one versioned manifest. Retain the rules/library/prompt revisions used before tuning. A changed source or split requires a new corpus revision and documented review.
5. Keep heldout labels out of tuning sessions. Only development records can seed a human-reviewed exact library; never automatically import model predictions or heldout examples. The current application library remains `empty-v1`.

Existing `fixtures/gate1.txt` and `fixtures/eight-groups.txt` are engineering regressions. They cannot establish human-heldout quality. Reviewed-record import, near-duplicate screening, corpus freeze and measured evaluation remain pending. The draft worksheet and integrity checker do not satisfy these requirements.

## Groq evidence preparation

The three Groq catalog entries are public reference pages only. The collector makes no authenticated account or model API requests and never reads `.env`. A downloaded pricing/model page cannot establish the key's account, no-billing settings, exclusive usage or current account quotas.

Keep a private evidence record containing:

- Captured official model/rate-limit/pricing references and their dates/hashes; separately verify applicable service terms and no-billing conditions.
- Chosen model ID and context/output constraints, plus nonsecret evidence that the key belongs to the intended account. Never record the key itself.
- Actual account/model request and token limits in every active window, prior usage/reset evidence and exclusive-account usage confirmation.
- Token-estimator verification against the selected model's authoritative tokenizer/framing or separately authorized measured usage. Record exact payload hashes, measured input tokens and conservative estimates; invented estimates are not measurements.
- Human verifier/date, expiry no later than 24 hours after verification, and remaining uncertainties.

Only verified evidence can populate the existing [readiness record](../groq-readiness.example.json), following [README setup](../README.md#groq-readiness-and-authorization). Keep Groq disabled until readiness and scoped live-call authorization exist. No specific model/free limit is asserted here, and no private readiness record has been created.

## Checks

`python -m unittest discover -s scripts/tests -v` checks setup, collection and offline review. The ten review tests cover source/offset tampering, Unicode/CRLF preservation, split duplicates, path/size limits, safe errors, blank labels and preservation of existing edits. `python scripts/check.py` includes these tests. Mock captures are fixtures; G2-03, G2-05 and G2-07 remain pending until their actual evidence is observed.
