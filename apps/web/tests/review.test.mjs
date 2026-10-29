import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { buildSync } from 'esbuild';

// Compile the real TSX with the already-installed Vite dependency, entirely in memory.
const require = createRequire(import.meta.url);
function component(name, exported = 'default') {
  const code = buildSync({ entryPoints: [fileURLToPath(new URL(`../src/${name}.tsx`, import.meta.url))], bundle: true,
    write: false, platform: 'node', format: 'cjs', jsx: 'automatic', external: ['react', 'react/jsx-runtime'] }).outputFiles[0].text;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', code)(require, module, module.exports);
  return module.exports[exported];
}
const ReportView = component('ReportView');
const ReviewOptions = component('ReviewOptions');
const URLInput = component('URLInput');
const CandidateList = component('URLInput', 'CandidateList');
const DocumentHistory = component('DocumentHistory');
const VersionList = component('DocumentHistory', 'VersionList');
const ComparisonView = component('ComparisonPanel', 'ComparisonView');
const DocumentDelete = component('DocumentDelete');
const score = { best: 80, worst: 0, coverage: .5, grade: null, scope_consistent: true };
const baseline = { score, clauses: [], categories: [], supported_groups: [1, 2, 3], engine: {} };
const report = { ...baseline, id: 'report', document_type: 'terms', title: 'Saved review', captured_at: '2026-10-28T00:00:00Z',
  requested_mode: 'hybrid', effective_mode: 'rules_only', text: '<script>private source</script>', rules_baseline: baseline,
  accepted_ai_findings: 0, degradation_reason: 'unverified_quota', degradation_message: 'External review limits are not verified. The result uses local rules.' };

test('unavailable external review is disabled and consent is never preselected', () => {
  const unavailable = renderToStaticMarkup(React.createElement(ReviewOptions, { mode: 'rules_only', consent: false, available: false, onMode() {}, onConsent() {} }));
  assert.match(unavailable, /value="hybrid" disabled/);
  assert.match(unavailable, /External AI review is unavailable/);
  const hybrid = renderToStaticMarkup(React.createElement(ReviewOptions, { mode: 'hybrid', consent: false, available: true, onMode() {}, onConsent() {} }));
  assert.match(hybrid, /type="checkbox" required/);
  assert.match(hybrid, /selected clauses and their context.*Groq/);
  assert.doesNotMatch(hybrid, /checked=""/);
});

test('degraded reports disclose requested and effective modes with a saved baseline', () => {
  const html = renderToStaticMarkup(React.createElement(ReportView, { report }));
  assert.match(html, /Requested: Hybrid.*Saved result: Rules-only/);
  assert.match(html, /External review limits are not verified/);
  assert.match(html, /Show rules-only baseline/);
  assert.match(html, /&lt;script&gt;private source&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>private source/);
});

test('AI findings show their source and model; older reports remain readable', () => {
  const finding = { source: 'ai', ai_call_id: 'call', category: 1, rule_id: 'ai:1:1', quote: 'Clause', start: 0, end: 6, rating: 'concerning', severity: 3, explanation: 'Synthetic finding.' };
  const hybrid = { ...report, text: 'Clause', effective_mode: 'hybrid', degradation_reason: null, accepted_ai_findings: 1,
    ai_provenance: [{ id: 'call', model: 'fixture-model' }], clauses: [{ id: 1, text: 'Clause', substantive: true, findings: [finding] }] };
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: hybrid }));
  assert.match(html, /AI review/);
  assert.match(html, /Model: fixture-model/);
  const legacy = renderToStaticMarkup(React.createElement(ReportView, { report: { ...report, requested_mode: 'rules_only', rules_baseline: null, degradation_reason: null } }));
  assert.match(legacy, /not stored with this earlier report/);
});

test('the scoring profile control is labeled and unavailable views stay disabled on older reports', () => {
  const legacy = renderToStaticMarkup(React.createElement(ReportView, { report }));
  assert.match(legacy, /Scoring profile<select[^>]*aria-describedby="profile-help"/);
  assert.match(legacy, /value="privacy_first" disabled/);
  const profiles = { revision: 'fixture-revision', balanced: { score, weights: {} },
    privacy_first: { score: { ...score, best: 65 }, weights: {} } };
  const current = renderToStaticMarkup(React.createElement(ReportView, { report: { ...report, score_profiles: profiles } }));
  assert.doesNotMatch(current, /value="privacy_first" disabled/);
  assert.match(current, /Both use the same saved evidence/);
  assert.match(current, /aria-live="polite" aria-atomic="true"/);
});

test('URL discovery offers explicit selection with escaped labels and no submit buttons', () => {
  const input = renderToStaticMarkup(React.createElement(URLInput, { value: '', csrf: 'test', onChange() {}, onSelect() {}, onPending() {} }));
  assert.match(input, /type="url" required/);
  assert.match(input, /type="button" disabled="">Find policy links/);
  const html = renderToStaticMarkup(React.createElement(CandidateList, { result: { limitations: [], candidates: [
    { title: '<script>source</script>', url: 'https://example.com/privacy', document_type: 'privacy' },
  ] }, onSelect() {} }));
  assert.match(html, /type="button">Select &lt;script&gt;source&lt;\/script&gt;/);
  assert.match(html, /then choose Analyze policy/);
  assert.doesNotMatch(html, /type="submit"|<script>|href=/);
});

test('saved URL metadata is escaped and raw snapshots are not rendered', () => {
  const html = renderToStaticMarkup(React.createElement(ReportView, { report: { ...report, source_kind: 'url', capture: {
    source_url: 'https://example.com/?q=<script>', final_url: 'https://example.com/privacy', content_type: 'text/html',
    text_sha256: 'a'.repeat(64), raw_base64: 'private-raw-snapshot',
  } } }));
  assert.match(html, /Captured page details/);
  assert.match(html, /https:\/\/example.com\/\?q=&lt;script&gt;/);
  assert.doesNotMatch(html, /private-raw-snapshot|<script>/);
});

test('document actions distinguish saved reanalysis from refreshing the current source', () => {
  const props = { report: { ...report, document_id: 'doc', version_id: 'version', version_number: 2, revision_number: 3 }, csrf: 'test',
    hybridAvailable: false, activeJob: false, onJob() {}, onOpen() {} };
  const html = renderToStaticMarkup(React.createElement(DocumentHistory, props));
  assert.match(html, /version 2, report revision 3/);
  assert.match(html, /Replacement policy text<textarea/);
  assert.match(html, /value="reanalysis">Reanalyze saved version 2/);
  assert.match(html, /value="hybrid" disabled/);
  const url = renderToStaticMarkup(React.createElement(DocumentHistory, { ...props, report: { ...props.report, source_kind: 'url' } }));
  assert.match(url, /Check URL for changes/);
  assert.doesNotMatch(url, /Replacement policy text/);
});

test('versions provide separate explicit selection and report reopening', () => {
  const history = { items: [{ id: 'v2', version_number: 2, captured_at: '2026-10-29T10:00:00Z', analysis_count: 3, latest_analysis_id: 'a3' }] };
  const html = renderToStaticMarkup(React.createElement(VersionList, { history, onSelect() {}, onOpen() {} }));
  assert.match(html, /3 saved report revisions/);
  assert.match(html, /type="button">Select version 2/);
  assert.match(html, /type="button">Open latest report for version 2/);
  assert.doesNotMatch(html, /type="submit"/);
});

test('two review forms use distinct accessible hint IDs', () => {
  const props = { mode: 'rules_only', consent: false, available: false, onMode() {}, onConsent() {} };
  const html = renderToStaticMarkup(React.createElement('div', null, React.createElement(ReviewOptions, props), React.createElement(ReviewOptions, props)));
  const references = [...html.matchAll(/aria-describedby="([^"]+)"/g)].map(match => match[1]);
  assert.equal(references.length, 2);
  assert.notEqual(references[0], references[1]);
});

test('comparisons escape passages and distinguish text from interpretation changes', () => {
  const side = { title: '<script>policy</script>', version_number: 1, revision_number: 1, score,
    requested_mode: 'hybrid', effective_mode: 'rules_only', degradation_message: 'External review unavailable.' };
  const clause = { text: '<img src=x onerror=alert(1)> 📜', substantive: true, findings: [] };
  const html = renderToStaticMarkup(React.createElement(ComparisonView, { data: {
    kind: 'versions', profile: 'balanced', left: side, right: side,
    summary: { source_text_changed: false, text_changes: 0, interpretation_changes: 1 },
    limits: { similarity_limited: true },
    passages: [{ left: clause, right: clause, text_change: 'unchanged', interpretation_changed: true, similarity: null }],
  } }));
  assert.match(html, /Text: unchanged/);
  assert.match(html, /Interpretation: changed findings or status/);
  assert.match(html, /Similarity matching reached its limit/);
  assert.match(html, /External review unavailable/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt; 📜/);
  assert.doesNotMatch(html, /<script>|<img/);
});

test('document deletion initially exposes only the confirmation entry point', () => {
  const html = renderToStaticMarkup(React.createElement(DocumentDelete, { documentId: 'doc', csrf: 'test',
    onAccepted() { assert.fail('Rendering must never delete a document'); } }));
  assert.match(html, /type="button">Delete all versions of this document/);
  assert.match(html, /Active jobs must finish first/);
  assert.doesNotMatch(html, />Confirm deletion<|type="submit"/);
});
