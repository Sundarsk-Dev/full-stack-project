# TermsLens: Terms & Conditions Analyzer
### Full end-to-end build plan (software-first, AI-last, powered by Groq)

---

## 0. TL;DR

TermsLens takes a website URL (or pasted legal text), finds the Terms, Privacy, Cookie and Refund documents, splits them into clauses, and rates each clause as **good, neutral, concerning or invasive** from the user's point of view. The user gets an A to F grade, a plain-English summary, and red flags with the exact quoted text.

**Core design principle:** *normal software does as much of the work as possible, and AI is called only for the small set of clauses that software cannot judge confidently.* AI is a fallback specialist, not the engine. Scoring, grading, caps, diffing, caching, missing-clause detection, alerts and verification are all deterministic code.

**AI provider:** Groq API (free tier, OpenAI-compatible, very fast), wrapped behind a provider interface so it can be swapped later.

---

## 1. The idea

Nobody reads Terms & Conditions. TermsLens does it for them:

1. Find the legal documents of a site
2. Break them into clauses
3. Classify each clause (data sharing, arbitration, content license, auto-renewal, and so on)
4. Rate it and explain why it matters, quoting the original text
5. Score the whole document and compare it with other sites
6. Watch for changes and alert users when terms get worse

**Why it works as an end-to-end project:** a real data pipeline (scraping, parsing, NLP), a versioned relational database, async job queues, a REST API, a rich frontend, caching and cost control, security hardening (SSRF, prompt injection), evaluation, and CI/CD.

---

## 2. Features

### MVP
- Submit a URL or paste text, then get an analysis report
- Auto-discovery of Terms, Privacy, Cookie and Refund pages
- Clause-level rating, severity and plain-English explanation
- Overall grade plus per-category scores
- Quoted evidence for every flag (nothing is a black box)
- Report history for logged-in users and shareable public report pages

### Version 2
- Change monitoring: weekly re-fetch, diff, alert
- Side-by-side comparison of two sites
- User preference profile that re-weights scores
- Leaderboard of best and worst policies
- PDF export

### Stretch
- Browser extension with a grade badge on any site
- Chat with a policy (RAG over clauses)
- Regulation checklists: GDPR, CCPA, India's DPDP Act
- Public API with API keys and an embeddable badge

---

## 3. Design principle: software first, AI last

### 3.1 The decision ladder

Every document and every clause moves down this ladder and **stops at the first step that can answer confidently**. AI is the last rung.

| Step | Mechanism | Uses AI? | Typical outcome |
|---|---|---|---|
| 0 | **Document cache**: same URL and same content hash as a previous analysis | No | Whole report reused instantly |
| 1 | **Boilerplate filter**: contact details, definitions, headers, "last updated" lines, standard severability text | No | Clause skipped (marked neutral/boilerplate) |
| 2 | **Clause library exact match**: normalized-text hash matches a previously rated clause | No | Rating reused |
| 3 | **Rule engine**: regex and keyword patterns for known bad or good phrases (arbitration, class waiver, perpetual license, "sell your data", and so on) | No | High-confidence rating plus templated explanation |
| 4 | **Semantic match**: local embedding similarity against the verified clause library (pgvector) | No (small local model) | Rating reused if similarity is above a threshold |
| 5 | **LLM (Groq)**: only for clauses still unresolved or low-confidence, and only if they pass the relevance gate | **Yes** | Structured JSON rating plus explanation |
| 6 | **Escalation**: a larger model, only for low-confidence or high-severity LLM results | Yes (rare) | Second opinion |
| 7 | **Software takes over again**: verify quotes, compute scores, apply caps, detect missing clauses, write the report | No | Final report |

### 3.2 What AI does and does not do

| Task | Done by | Why |
|---|---|---|
| Finding legal pages | Software | Link scoring and sitemap checks are deterministic |
| Fetching and cleaning | Software | Parsing, not reasoning |
| Splitting into clauses | Software | Headings and numbering rules |
| Detecting well-known bad phrases | Software (rules) | Faster, free, and explainable |
| Reusing past ratings | Software (hash and vector match) | Zero cost, consistent results |
| Judging **ambiguous or novel** clauses | **AI** | The only step that truly needs language understanding |
| Rewriting a finding in plain English | Template for rule hits; **AI** only for novel clauses | Templates are free and consistent |
| Overall summary and TL;DR | **AI, one call per document** (or a template fallback) | Short, bounded call |
| Scoring, grading, caps | Software | Must be deterministic and auditable |
| Verifying AI output (quote must exist in clause, schema valid) | Software | Guards against hallucination |
| Missing-clause detection | Software | Checklist against the taxonomy |
| Diffing versions, alerts, scheduling | Software | No reasoning needed |

### 3.3 The relevance gate (when is AI actually allowed to run?)

A clause is sent to the LLM **only if all of these are true**:

1. Not a cache hit and not in the clause library (exact or semantic)
2. Not matched by a high-confidence rule
3. Not boilerplate (length above a minimum, and not a pure definitions/contact/heading clause)
4. Contains at least one **signal term** from the taxonomy (for example: data, share, third part, license, content, arbitrat, liab, indemn, terminat, renew, refund, cookie, track, retain, delete, children, change, notice)
5. The per-analysis AI budget has not been exceeded (see 3.4)

```python
def needs_ai(clause, ctx) -> tuple[bool, str]:
    if ctx.cache.hit(clause.hash):                return False, "cache_hit"
    if ctx.library.exact(clause.norm_hash):       return False, "library_exact"
    if rules.match(clause) and rules.confidence >= 0.85:
                                                  return False, "rule_confident"
    if ctx.library.semantic(clause.embedding) >= 0.90:
                                                  return False, "library_semantic"
    if is_boilerplate(clause):                    return False, "boilerplate"
    if not has_signal_terms(clause):              return False, "no_signal"
    if ctx.budget.exhausted():                    return False, "budget_exhausted"
    return True, "ambiguous_or_novel"
```

Every decision (including the reason string) is logged so you can later measure how often AI is used and why.

### 3.4 Budget and rate-limit protection

- **Per-analysis cap:** for example at most 12 LLM clause-calls and 1 summary call per document. Remaining unresolved clauses are marked "unrated (AI limit reached)" instead of being silently dropped.
- **Batching:** group 5 to 8 clauses per request to reduce the number of calls.
- **Token bucket in Redis:** keep outgoing calls below Groq's per-model limits (read your exact limits in the Groq console; limits apply per organization and per model).
- **429 handling:** respect `Retry-After`, use exponential backoff with jitter, and re-queue the job rather than failing it.
- **Daily quota counter:** if the app's daily AI allowance is nearly used up, new analyses run in **rules-only mode** and the report shows a clear "AI-assisted analysis unavailable right now, rule-based results only" badge.
- **Circuit breaker:** after N consecutive provider failures, stop calling the AI for a cooldown period and degrade gracefully.
- **Anonymous users:** lower limits (and optional CAPTCHA) so strangers cannot burn your free quota.
- **Determinism:** temperature 0, so the same clause gets the same answer.

### 3.5 The learning loop (the system gets cheaper over time)

When the LLM rates a clause with high confidence and the result passes verification (valid schema, evidence quote found in the clause text), save it to the **`clause_library`** (normalized hash plus embedding plus label). The next time a similar clause appears on any site, steps 2 and 4 resolve it with **no AI call**.

- Library entries have a `verified` flag. Auto-added entries start unverified; an admin page lets you confirm or correct them.
- Corrections feed back into the golden evaluation set.
- Over time, most common clauses (arbitration, content license, cookies, retention) never need AI again.

### 3.6 Illustrative savings (an estimate to design against, not a measurement)

A 10,000-word policy might produce around 60 clauses:

| Resolved by | Clauses |
|---|---|
| Boilerplate filter | about 15 |
| Rules | about 20 |
| Library (exact or semantic) | about 15 |
| **Sent to AI** | **about 10 (1 to 2 batched calls)** |

Plus one summary call. That is roughly **2 to 3 AI calls per fresh document** and often **zero** for a re-analysis. Track the real "AI call ratio" on your dashboard and tune thresholds against it.

---

## 4. AI provider: Groq

### 4.1 Why Groq fits this project
- **Free tier, no credit card required.** Usage is limited by rate limits (requests and tokens per minute or per day), so you check the current numbers in your Groq console under Limits. Limits apply per organization and per model.
- **Very fast inference**, which makes the progress UI feel responsive.
- **OpenAI-compatible API** (`https://api.groq.com/openai/v1`) and an official Python SDK, so integration is easy and a provider swap is cheap.
- **Open models** (Llama family, GPT-OSS and others), which are good enough for clause classification when the prompt is tight and output is validated.
- **Caveat:** the free tier is for development and moderate use. Heavy public traffic will need either caching (which this design already prioritizes) or the paid Developer tier. Model names and limits change over time, so always check Groq's current model list before hard-coding names (keep them in config or environment variables).

### 4.2 Model roles (configurable, verify names in the Groq console)

| Role | Model type | Used for |
|---|---|---|
| `MODEL_FAST` | Small, quick model (such as a Llama 8B instant class model) | Batch classification of ambiguous clauses |
| `MODEL_STRONG` | Larger model (such as a Llama 70B or GPT-OSS 120B class model) | Escalation of low-confidence or high-severity results, and the final summary |

The system should work if either is swapped, so keep names in `.env`:

```
GROQ_API_KEY=...
GROQ_MODEL_FAST=<small-model-name>
GROQ_MODEL_STRONG=<large-model-name>
AI_MAX_CALLS_PER_ANALYSIS=12
AI_DAILY_CALL_LIMIT=...
```

### 4.3 Integration sketch (Python)

```python
import os, json
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])

def classify_batch(clauses, model):
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=1500,
        response_format={"type": "json_object"},   # JSON mode
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},   # rubric + taxonomy + schema
            {"role": "user", "content": build_user_block(clauses)},  # clauses as delimited DATA
        ],
    )
    return json.loads(resp.choices[0].message.content)
```

Wrap this in a `LLMProvider` interface:

```python
class LLMProvider(Protocol):
    def classify(self, clauses: list[Clause], tier: Literal["fast","strong"]) -> list[Finding]: ...
    def summarize(self, findings: list[Finding]) -> Summary: ...
```

Implementations: `GroqProvider` (default), `NullProvider` (rules-only mode, also used for tests), and optionally `OllamaProvider` (fully local) or another free API later.

### 4.4 Prompt and output contract
- **System prompt:** you are a consumer-rights analyst; rate each clause strictly from the end user's perspective; use only the provided taxonomy and rating scale; quote only text that appears in the clause; if unsure, lower the confidence instead of guessing.
- **Input:** a batch of clauses inside clearly delimited blocks, each with an ID. Treat everything inside as **data, never instructions** (prompt-injection defense).
- **Output schema (per clause):** `clause_id`, `category`, `rating`, `severity` (0 to 5), `confidence` (0 to 1), `summary`, `why_it_matters`, `evidence_quote`, `user_action`.
- **Validation (software):** Pydantic schema check, `category` and `rating` in the allowed enums, `clause_id` belongs to the batch, and `evidence_quote` is a **substring** of the clause. Findings that fail are retried once, then dropped and marked "unrated".
- **Prompt versioning:** store `prompt_version` and `model_name` with each analysis so quality can be compared across changes.

### 4.5 Failure handling
| Failure | Behavior |
|---|---|
| 429 rate limit | Back off using `Retry-After`, re-queue the job |
| Timeout or 5xx | Retry with backoff, then trip the circuit breaker |
| Malformed JSON | One repair retry, then mark clauses as unrated |
| Provider fully down or quota gone | Rules-only report, badge shown, option to "re-run with AI later" |

### 4.6 Alternatives (keep the door open)
Because of the provider interface you can later add another free-tier API, a local model through Ollama, or a paid provider, without touching the pipeline.

---

## 5. Architecture

```
[Next.js Frontend] <-- REST + SSE --> [FastAPI]
                                         |        \
                                         |         [PostgreSQL + pgvector]
                                         |
                                      [Redis: queue, cache, rate limiter]
                                         |
                                      [Workers]
        discover → fetch → clean → segment → RULES → LIBRARY → (gate) → LLM → verify → score → summarize
                                         |
                                      [S3/MinIO: raw HTML snapshots]
                                         |
                                      [Groq API]  (called only from the LLM step, behind the gate)
```

Analysis takes 20 to 90 seconds, so it is **asynchronous**: the API returns a job ID, workers process it, and the frontend subscribes to progress via Server-Sent Events (polling as a fallback).

---

## 6. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js, TypeScript, Tailwind, Recharts | SSR for shareable public report pages |
| API | FastAPI (Python) | Auto OpenAPI docs; same language as NLP and scraping |
| Jobs | Celery or RQ with Redis | Retries, scheduling (beat) |
| Database | PostgreSQL + pgvector | Relational data plus embeddings |
| Scraping | httpx, trafilatura, Playwright fallback | Static first, headless browser if needed |
| Local embeddings | sentence-transformers (small model such as MiniLM, 384 dimensions) | Free, CPU-friendly, powers semantic matching with no API cost |
| LLM | Groq API via `LLMProvider` | Gated, batched, cached |
| Auth | JWT with refresh tokens (or Auth.js) | HTTP-only cookies |
| Storage | S3-compatible (MinIO locally) | Raw HTML snapshots |
| DevOps | Docker Compose, GitHub Actions, Alembic | Reproducible dev and CI |
| Observability | Structured logs, Sentry, metrics | Track AI call ratio and cost |

A JS-only alternative: Node with NestJS or Express, BullMQ, Prisma. The architecture is identical.

---

## 7. Pipeline stages in detail

### Stage 1: Discovery (software)
- Fetch the homepage, extract links, score by anchor text and URL pattern (`terms`, `tos`, `privacy`, `legal`, `cookie`, `refund`, `eula`, `policies`)
- Check footer links first, then `sitemap.xml`, then common paths (`/terms`, `/privacy-policy`)
- Output `(doc_type, url)` pairs

### Stage 2: Fetch and clean (software)
- Static fetch first; if the cleaned text is under about 500 words, retry with Playwright
- Remove nav, footer, banners and scripts; keep heading structure
- Save the raw HTML and compute a SHA-256 hash of the cleaned text
- **Cache check:** same URL and same hash means reuse the earlier analysis (Step 0 of the ladder)

### Stage 3: Segmentation (software)
- Split on headings and numbered sections, then paragraphs; target 80 to 300 words per clause
- Store position, parent heading, normalized text and normalized hash

### Stage 4: Resolve clauses (software, then gated AI)
1. Boilerplate filter
2. Library exact match
3. Rule engine (regex and keywords) with templated explanations
4. Local embedding and semantic library match
5. Relevance gate, then batched Groq call
6. Optional escalation to the stronger model

### Stage 5: Verify (software)
- Schema validation, enum checks, evidence-quote substring check
- Drop or flag anything that fails

### Stage 6: Score (software)
- Category scores, overall grade, grade caps, missing-clause penalties (section 9)

### Stage 7: Summarize
- One bounded summary call (TL;DR, top 5 things to know, good news), or a template-based summary if AI is unavailable or the budget is spent

### Stage 8: Learn (software)
- Add verified high-confidence AI results to the clause library

---

## 8. Clause taxonomy: what is good, neutral or bad

Store this as a config (JSON or DB table). It drives rules, prompts, scoring and the "missing clause" checks, so you can tune it without redeploying.

| Category | Good for the user | Neutral / watch | Bad / invasive |
|---|---|---|---|
| **Data collected** | Only what the service needs | Usage and device data | Contacts, precise location, biometrics, or data from other apps without clear need |
| **Data sharing** | No sharing, or only with contracted processors | Shared with analytics providers | Shared with advertisers and data brokers; vague "partners" |
| **Data selling** | Explicit "we never sell your data" | Ambiguous wording | Explicit sale, or "sharing" defined broadly enough to include sale |
| **Retention** | Fixed period; deleted on request | "As long as necessary" | Kept indefinitely or after account deletion |
| **User rights** | Access, deletion and portability are easy | Rights exist but require emailing support | No deletion right, or rights limited to certain regions |
| **Tracking and cookies** | Opt-in with granular controls | Standard analytics cookies | Cross-site tracking or fingerprinting with no opt-out |
| **Content ownership** | You own it; license limited to running the service | Broad but revocable license | Perpetual, irrevocable, sublicensable license, including for ads or AI training |
| **AI and data use** | Explicit opt-in for model training | Mentioned but unclear | Your content trains AI by default, no opt-out |
| **Changes to terms** | Advance notice and a chance to reject | Posted on the site | Can change anytime, no notice; continued use means acceptance |
| **Dispute resolution** | Court access, local jurisdiction | Distant venue | Mandatory arbitration plus class-action waiver |
| **Liability** | Standard disclaimers | Capped at fees paid | Excluded even for their negligence or data breaches |
| **Indemnification** | Limited to your misuse | Broad but standard | You cover their legal costs for almost anything |
| **Account termination** | Notice, reason, data export window | Termination for violations | Anytime, any reason, data deleted, no refund |
| **Payments and subscriptions** | Clear pricing, easy cancel, prorated refunds | Auto-renewal with reminder | Hidden auto-renewal, no refunds, price changes without notice |
| **Security and breaches** | Breach notification commitment | "Reasonable measures" | Explicitly disclaims responsibility for breaches |
| **Children** | Age limits and parental consent | Age limit stated only | Collects children's data with no age gate |
| **Third-party SDKs and links** | Disclosed and limited | Generic disclaimer | Many embedded trackers, undisclosed |
| **Governing law** | Your local law applies | Neutral major jurisdiction | Obscure venue meant to deter claims |

Each category also stores: `signal_terms`, `rule_patterns` (with confidence), a `template_explanation` for rule hits, and a `required` flag for missing-clause detection.

---

## 9. Scoring model (100% software)

**Per clause:** severity from 0 (positive) to 5 (severely invasive), plus a rating label.

**Category score (0 to 100):**
```
category_score = 100 - (weighted average severity / 5) * 100
```

**Overall grade:** weighted sum of category scores, with user-adjustable weights.

| Group | Default weight |
|---|---|
| Data collection, sharing, selling | 30% |
| User rights and retention | 15% |
| Content ownership and AI use | 15% |
| Legal rights (arbitration, liability, jurisdiction) | 15% |
| Payments and termination | 10% |
| Transparency (change notices, clarity) | 10% |
| Security | 5% |

**Grade bands:** A 90+, B 75 to 89, C 60 to 74, D 40 to 59, F below 40.

**Grade caps** (so one terrible clause cannot hide in a friendly document):
- Explicit sale of personal data: grade capped at C
- Perpetual content license plus mandatory arbitration: capped at D

**Missing-clause detection:** compare found categories against the taxonomy's `required` list. No retention period, no deletion right, no breach notification: these are flagged as "absent" findings with their own severity.

**Confidence handling:** unrated clauses (AI limit reached or failed validation) are excluded from the average, and the report shows a "coverage" figure such as "52 of 58 clauses rated".

---

## 10. Database schema (PostgreSQL)

```sql
users (
  id UUID PK, email UNIQUE, password_hash, created_at,
  preferences JSONB            -- category weights, regions, alert settings
)

sites (
  id UUID PK, domain UNIQUE, name, first_seen_at, last_analyzed_at
)

documents (
  id UUID PK, site_id FK, doc_type ENUM('terms','privacy','cookies','refund','eula','other'),
  url, current_version_id FK
)

document_versions (
  id UUID PK, document_id FK, fetched_at, content_hash,
  cleaned_text TEXT, raw_html_s3_key, word_count
)

analyses (
  id UUID PK, document_version_id FK, mode ENUM('full','rules_only'),
  model_fast, model_strong, prompt_version,
  status ENUM('queued','running','done','failed'),
  overall_score INT, grade CHAR(1), coverage REAL, summary TEXT,
  ai_calls INT, created_at, completed_at
)

clauses (
  id UUID PK, document_version_id FK, position INT, heading TEXT,
  text TEXT, norm_hash TEXT, embedding VECTOR(384)
)

clause_findings (
  id UUID PK, analysis_id FK, clause_id FK,
  category, rating, severity SMALLINT, confidence REAL,
  summary TEXT, why_it_matters TEXT, evidence_quote TEXT,
  source ENUM('boilerplate','library_exact','rule','library_semantic','llm_fast','llm_strong','human'),
  decision_reason TEXT            -- why this path was chosen (audit trail)
)

clause_library (
  id UUID PK, norm_hash UNIQUE, embedding VECTOR(384),
  canonical_text TEXT, category, rating, severity, explanation TEXT,
  origin ENUM('seed','rule','llm','human'), verified BOOL, times_used INT,
  created_at
)

ai_calls (
  id UUID PK, analysis_id FK, model, tier ENUM('fast','strong','summary'),
  clause_count INT, prompt_tokens INT, completion_tokens INT,
  latency_ms INT, status, error, created_at
)

category_scores (analysis_id FK, category, score INT, PRIMARY KEY (analysis_id, category))

subscriptions (id UUID PK, user_id FK, site_id FK, notify_on ENUM('any_change','worse_only'), created_at)

change_events (
  id UUID PK, document_id FK, old_version_id FK, new_version_id FK,
  diff JSONB, score_delta INT, detected_at
)

saved_reports (user_id FK, analysis_id FK, note TEXT, created_at)

jobs (id UUID PK, requested_by FK NULL, url, status, progress_stage, error, created_at)

api_keys (id, user_id FK, key_hash, rate_limit, created_at)   -- stretch
```

**Indexes:** `sites(domain)`, `document_versions(document_id, fetched_at DESC)`, `clauses(norm_hash)`, `clause_findings(analysis_id, severity DESC)`, a vector index (HNSW or IVFFlat) on `clauses.embedding` and `clause_library.embedding`.

**Why version everything:** analyses point at document *versions*, which gives you history, diffs and cache hits for free.

---

## 11. API design (REST)

**Auth**
- `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`, `GET /me`
- `PATCH /me/preferences`

**Analysis**
- `POST /analyses` with `{url}` or `{text}` returns `202 {job_id}`
- `GET /jobs/{id}` gives status and current stage
- `GET /jobs/{id}/events` streams progress (SSE)
- `GET /analyses/{id}` gives the full report (including `mode`, `coverage`, `ai_calls`)
- `GET /analyses/{id}/findings?category=&rating=&min_severity=&source=`
- `POST /analyses/{id}/rerun-ai` re-runs unrated clauses when quota is available
- `GET /analyses/{id}/export.pdf`

**Sites**
- `GET /sites/{domain}`, `GET /sites/{domain}/history`
- `GET /sites?sort=score&order=asc` (leaderboard)
- `GET /compare?a=domainA&b=domainB`

**User library and monitoring**
- `GET/POST/DELETE /me/reports`
- `POST/DELETE /me/subscriptions`

**Admin**
- `GET /admin/library?verified=false`, `PATCH /admin/library/{id}` (verify or correct)
- `GET /admin/ai-usage` (calls per day, AI call ratio, failure rate)

**Other**
- `POST /analyses/{id}/ask` with `{question}` (stretch RAG)
- `GET /health`

All endpoints use Pydantic validation, consistent error shapes and a published OpenAPI spec.

---

## 12. Frontend pages and components

| Page | Contents |
|---|---|
| **Landing** | URL input, example reports, how it works |
| **Progress** | Stepper: Finding → Fetching → Splitting → Rule checks → AI review (only if needed) → Scoring |
| **Report** | Grade badge, TL;DR, radar chart, top red flags, good news, coverage and "analysis mode" badge |
| **Clause explorer** | Original text with highlights on the left, annotation on the right; filter by category, rating, severity and source (rule, library or AI) |
| **Site history** | Score timeline and diff viewer |
| **Compare** | Two sites side by side with category bars |
| **Dashboard** | Saved reports, subscriptions, alerts |
| **Preferences** | Weight sliders, region toggles, alert settings |
| **Leaderboard** | Sortable best and worst list |
| **Admin** | Clause library review queue, AI usage charts |

**UX details:**
- Color, icon **and** text for ratings (never color alone)
- Every flag links to its highlighted passage
- A small label shows how each finding was produced ("Rule match", "Known clause", "AI-reviewed"), which builds trust
- Visible disclaimer: "Automated analysis, not legal advice"
- Friendly error states for sites that block scrapers, with a "paste the text instead" option

---

## 13. Background jobs and monitoring

- **Analysis job:** the full pipeline with the gate and budget logic
- **Weekly re-check (scheduler):** re-fetch subscribed documents, compare hashes; if changed, diff the text, resolve **only changed clauses** through the same ladder, compute a score delta, write a `change_event`
- **Notifications:** email through SMTP or a provider, for example "Spotify updated its Privacy Policy. 2 new concerning clauses."
- **Retries:** exponential backoff and a dead-letter queue for failed fetches
- **Politeness:** respect `robots.txt`, set a clear User-Agent, rate-limit per domain
- **Maintenance jobs:** prune old snapshots, recompute library usage counts, rebuild vector indexes if needed

---

## 14. Security and robustness

- **SSRF protection (critical):** users submit URLs your server fetches. Block private IP ranges, localhost, link-local addresses and cloud metadata (`169.254.169.254`); re-validate after every redirect; enforce size and time limits; only allow http and https.
- **Prompt injection:** policy text is untrusted. Place it in delimited data blocks, never let it alter instructions, and validate every output against the schema and the quote check.
- **API key safety:** the Groq key lives only on the server in environment variables, never in frontend code or the repo.
- **Abuse control:** rate limits per IP and per user, CAPTCHA for anonymous bursts, per-user AI allowances.
- **Auth hygiene:** argon2 or bcrypt hashing, short-lived access tokens, HTTP-only cookies.
- **Input and output safety:** validate everything, sanitize HTML, escape on render.
- **Privacy of pasted text:** text a user pastes may be private. Tell users it is sent to a third-party AI provider when AI is needed, offer a "do not store" option, and do not add pasted text to the public clause library by default.
- **Dogfooding:** write a clear privacy policy for TermsLens and run TermsLens on it.

---

## 15. Testing and evaluation

- **Unit tests:** link scoring, segmentation, boilerplate filter, rule patterns, gate logic (every branch of `needs_ai`), score math, grade caps, SSRF validator
- **Integration tests:** API with a test database; worker pipeline with `NullProvider` and mocked Groq responses
- **Contract tests:** LLM output schema, including bad-JSON and hallucinated-quote cases
- **Budget tests:** verify the per-analysis cap, rules-only fallback, 429 handling and circuit breaker
- **Frontend and E2E (Playwright):** submit a URL, watch progress, open the report, save it
- **Golden dataset:** hand-label 20 to 30 real policies (200 to 300 clauses) with expected category and rating. Track accuracy for rules only, rules plus library, and the full pipeline, so you can prove the AI layer adds value and see where the rules fall short.
- **Regression gate in CI:** re-run the golden set and fail if accuracy drops by more than a threshold after a prompt, rule or model change.
- **External benchmark:** compare grades with community ratings such as ToS;DR (check their license and terms first).
- **Key metrics to watch:** AI call ratio (AI-resolved clauses divided by total), average AI calls per analysis, cache hit rate, library hit rate, rate-limit errors, accuracy on the golden set.

---

## 16. DevOps

- `docker-compose.yml` services: `web`, `api`, `worker`, `scheduler`, `postgres`, `redis`, `minio`
- Alembic migrations and a seed script (sample sites, taxonomy config, seed clause library)
- GitHub Actions: lint, test, build images, deploy on merge to main
- Sentry for errors; metrics for job duration, failure rate, AI calls per analysis
- `.env.example` documenting every variable (including the Groq settings)
- A README with an architecture diagram and a short demo video

### Suggested folder structure

```
termslens/
├── apps/
│   ├── web/                  # Next.js frontend
│   └── api/
│       ├── app/
│       │   ├── main.py
│       │   ├── routers/      # auth, analyses, sites, admin
│       │   ├── models/       # SQLAlchemy models
│       │   ├── schemas/      # Pydantic schemas
│       │   ├── services/
│       │   │   ├── discovery.py
│       │   │   ├── fetcher.py
│       │   │   ├── cleaner.py
│       │   │   ├── segmenter.py
│       │   │   ├── rules/            # taxonomy.json, patterns, templates
│       │   │   ├── library.py        # exact and semantic clause matching
│       │   │   ├── gate.py           # needs_ai() and budget logic
│       │   │   ├── llm/
│       │   │   │   ├── provider.py   # LLMProvider protocol
│       │   │   │   ├── groq_provider.py
│       │   │   │   ├── null_provider.py
│       │   │   │   └── prompts.py
│       │   │   ├── verifier.py
│       │   │   ├── scoring.py
│       │   │   └── summarizer.py
│       │   ├── workers/      # Celery tasks, scheduler
│       │   └── security/     # ssrf.py, rate_limit.py
│       ├── migrations/
│       └── tests/
├── eval/                     # golden dataset and evaluation scripts
├── docker-compose.yml
└── README.md
```

---

## 17. Build roadmap

| Phase | Time | Deliverable |
|---|---|---|
| **0. Setup** | 2 to 3 days | Repo, Docker Compose, migrations, CI skeleton, scaffolds |
| **1. Rules-only analyzer on pasted text** | 1 week | Segmentation, taxonomy, rule engine, scoring, basic report. **No AI yet**, which proves how far software alone can go |
| **2. Add Groq behind the gate** | 4 to 5 days | `LLMProvider`, gate, batching, verification, budget and rate-limit protection, rules-only fallback |
| **3. URL ingestion** | 1 week | Discovery, fetching, caching, SSRF protection, async queue with live progress |
| **4. Clause library and local embeddings** | 4 to 5 days | Exact and semantic matching, learning loop, admin review page |
| **5. Accounts** | 4 to 5 days | Auth, saved reports, custom weights |
| **6. Report polish** | 1 week | Clause explorer, charts, missing-clause detection, PDF export, source labels |
| **7. Monitoring** | 1 week | Versioning, diffs, scheduled checks, alerts, history timeline |
| **8. Evaluation** | 3 to 4 days | Golden set, metrics, CI regression gate, AI usage dashboard |
| **9. Deploy and extras** | 1 week | Production deploy, compare page, leaderboard, optional browser extension |

Roughly 7 to 9 weeks part-time. The project is demo-able after Phase 3.

**Why Phase 1 has no AI:** it forces a strong rule engine and taxonomy first. Then Phase 2 only has to handle the genuinely hard residue, which is exactly the design goal.

---

## 18. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Free-tier rate limits hit | Gate, batching, caching, library, token bucket, daily counter, rules-only fallback |
| AI hallucinated ratings or quotes | Schema validation, quote substring check, rules as a cross-check, golden-set regression tests |
| Model names or limits change | Names in config, provider interface, check the Groq console for current limits |
| Sites block scrapers or render with JS | Playwright fallback, manual paste option, clear errors |
| Very long documents | Segmentation, batching, parallel workers, per-analysis AI cap with a coverage figure |
| Wrong entries enter the clause library | `verified` flag, admin review, only high-confidence and verified results are auto-added |
| Users mistake output for legal advice | Prominent disclaimers; framed as "impact on you" ratings |
| "Bad" is subjective | Transparent rubric, adjustable weights, always show the quoted text |
| Pasted private text sent to a third party | Notice to the user, no-store option, excluded from the public library |
| Legality and ethics of scraping | Fetch only public legal pages, respect robots.txt, rate-limit, store minimal data |

---

## 19. Stretch ideas

- **Browser extension:** grade badge on any site, plus a warning when you are about to click "I agree" on a signup page
- **Policy Q&A (RAG):** embed clauses locally, retrieve the relevant ones, and let the AI answer only from those with citations ("Can they use my photos for ads?")
- **Regulation checklist:** compare a policy with GDPR, CCPA and India's DPDP Act requirements
- **Industry benchmarks:** "Worse than 80% of fitness apps"
- **Embeddable badge:** `<img src="/badge/example.com.svg">`
- **Multi-language support:** analyze non-English policies and translate summaries
- **Fully local mode:** Ollama provider so the whole app runs with no external AI

---

## 20. What you will have built and learned

Relational modeling with versioning, async job pipelines, scraping, a layered rules-plus-embeddings-plus-LLM classification system, cost-aware AI integration with caching and rate limiting, structured output validation, SSRF and prompt-injection defense, real-time progress updates, data visualization, scheduled monitoring, evaluation against a labeled dataset, and containerized CI/CD deployment.

---

*TermsLens produces automated analyses for information purposes. It is not legal advice.*
