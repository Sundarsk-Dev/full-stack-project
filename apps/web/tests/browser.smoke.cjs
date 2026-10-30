// Browser interaction check with explicitly mocked API responses; not MySQL acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { chromium } = require(process.env.TERMSLENS_PLAYWRIGHT_MODULE || 'playwright');

const root = path.resolve(__dirname, '../../..');
const dist = path.resolve(__dirname, '../dist');

(async () => {
  const policy = 'A 📜 heading.\n' + await fs.readFile(path.join(root, 'eval/fixtures/gate1.txt'), 'utf8');
  const python = spawnSync(process.env.TERMSLENS_PYTHON || 'python', ['-X', 'utf8', '-c',
    'import json,sys; from app.analysis import analyze; from app.scoring import saved_profiles; result=analyze(sys.stdin.read(), "privacy"); result["score_profiles"]=saved_profiles(result, "privacy"); print(json.dumps(result))'],
    { cwd: path.join(root, 'apps/api'), input: policy, encoding: 'utf8', timeout: 10000 });
  assert.equal(python.status, 0, 'The synthetic analysis fixture must generate successfully.');
  const report = { ...JSON.parse(python.stdout), id: 'report-fixture', document_id: 'document-fixture', version_id: 'version-fixture',
    title: 'Browser fixture', document_type: 'privacy', created_at: '2026-10-28T00:00:00Z', captured_at: '2026-10-28T00:00:00Z',
    requested_mode: 'hybrid', effective_mode: 'rules_only', review_status: 'degraded',
    degradation_reason: 'unverified_quota', degradation_message: 'External review limits are not verified. The result uses local rules.',
    consent_provider: 'groq', consent_at: '2026-10-28T00:00:00Z', accepted_ai_findings: 0,
    rules_baseline: JSON.parse(python.stdout), ai_provenance: [] };
  const version = { id: report.version_id, version_number: 1, captured_at: report.captured_at, analysis_count: 1, latest_analysis_id: report.id };
  const other = { ...report, id: 'other-report', title: 'Earlier report', version_number: 1, revision_number: 2 };
  const comparison = { id: 'comparison-fixture', kind: 'versions', profile: 'balanced', left: report, right: other,
    summary: { source_text_changed: false, text_changes: 0, interpretation_changes: 1 }, limits: { similarity_limited: false },
    passages: [{ left: report.clauses[0], right: other.clauses[0], text_change: 'unchanged', interpretation_changed: true, similarity: null }], total: 1 };
  let signedIn = false, submitted = false, polls = 0, failLogin = false, reportReads = 0;
  let compared = false, deleted = false;
  let hybridAvailable = true;
  const browser = await chromium.launch({ headless: true, timeout: 10000 });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://127.0.0.1:4173/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const pathname = url.pathname;
      if (pathname.startsWith('/api/') && ['POST', 'DELETE'].includes(request.method())) assert.equal(request.headers().origin, 'http://127.0.0.1:4173');
      const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
      if (pathname === '/api/me') return json({ detail: 'Please log in.' }, 401);
      if (pathname === '/api/health') return json({ external_review: { available: hybridAvailable, provider: 'groq', reason: hybridAvailable ? 'configured' : 'verification_expired_or_invalid' } });
      if (pathname === '/api/auth/register' || pathname === '/api/auth/login') {
        if (failLogin) return json({ detail: 'Database unavailable in this test.' }, 503);
        signedIn = true;
        return json({ user: { id: 'browser-user', username: 'browser_user' }, csrf_token: 'fixture-csrf' }, pathname.endsWith('register') ? 201 : 200);
      }
      if (pathname === '/api/auth/logout') {
        assert.equal(request.headers()['x-csrf-token'], 'fixture-csrf');
        signedIn = false;
        return route.fulfill({ status: 204 });
      }
      if (pathname.startsWith('/api/')) assert.equal(signedIn, true, 'Private routes require a fixture login');
      if (pathname === '/api/deletions') return json({ items: deleted ? [{ document_id: report.document_id, state: 'complete', created_at: report.created_at }] : [], total: deleted ? 1 : 0 });
      if (pathname === '/api/documents/document-fixture' && request.method() === 'DELETE') {
        assert.equal(request.headers()['x-csrf-token'], 'fixture-csrf');
        deleted = true;
        return json({ document_id: report.document_id, state: 'pending' }, 202);
      }
      if (pathname === '/api/comparisons' && request.method() === 'POST') {
        assert.equal(request.headers()['x-csrf-token'], 'fixture-csrf');
        assert.deepEqual(request.postDataJSON(), { left_id: report.id, right_id: other.id, kind: 'versions', profile: 'balanced' });
        compared = true;
        return json({ comparison_id: comparison.id }, 201);
      }
      if (pathname === '/api/comparisons') return json({ items: compared ? [{ id: comparison.id, left_title: report.title, right_title: other.title, created_at: report.created_at }] : [], total: compared ? 1 : 0 });
      if (pathname === '/api/comparisons/comparison-fixture') return json(comparison);
      if (pathname === '/api/documents/document-fixture/versions') return json({ items: [version], total: 1, limit: 10, offset: 0, last_check: null });
      if (pathname === '/api/documents/document-fixture/versions/version-fixture/analyses') return json({ items: [{ ...report, revision_number: 1 }], total: 1, limit: 10, offset: 0 });
      if (pathname === '/api/discovery') {
        assert.equal(request.headers()['x-csrf-token'], 'fixture-csrf');
        assert.equal(request.postDataJSON().url, 'https://example.com/');
        return json({ candidates: [{ title: 'Privacy policy', url: 'https://example.com/privacy', document_type: 'privacy' }], limitations: ['static_same_origin_links_only'] });
      }
      if (pathname === '/api/analyses' && request.method() === 'POST') {
        assert.equal(request.headers()['x-csrf-token'], 'fixture-csrf');
        assert.ok(request.headers()['idempotency-key']);
        assert.equal(request.postDataJSON().text, policy);
        assert.equal(request.postDataJSON().mode, 'hybrid');
        assert.equal(request.postDataJSON().consent, true);
        assert.equal(request.postDataJSON().provider, 'groq');
        submitted = true;
        return json({ job_id: 'job-fixture', state: 'queued' }, 202);
      }
      if (pathname === '/api/analyses') return json({ items: submitted && !deleted ? [{ ...report, ...report.score }, { ...other, ...other.score }] : [], total: submitted && !deleted ? 2 : 0, limit: 10, offset: 0 });
      if (pathname === '/api/jobs/job-fixture') {
        const done = ++polls >= 2;
        return json({ id: 'job-fixture', document_id: report.document_id, state: done ? 'partial' : 'queued', stage: done ? 'complete' : 'queued',
          attempts: done ? 1 : 0, result_id: done ? 'report-fixture' : null, error: null, created_at: report.created_at });
      }
      if (pathname === '/api/analyses/report-fixture') { reportReads++; return json(report); }
      if (pathname.startsWith('/api/')) return json({ detail: 'Unsupported fixture route' }, 404);
      const file = path.resolve(dist, pathname === '/' ? 'index.html' : '.' + pathname);
      assert.ok(file.startsWith(dist + path.sep));
      try {
        const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
        return route.fulfill({ contentType, body: await fs.readFile(file) });
      } catch { return route.fulfill({ status: 404 }); }
    });
    await page.goto('http://127.0.0.1:4173/');
    await page.getByRole('button', { name: 'Create an account', exact: true }).click();
    await page.getByLabel('Username', { exact: true }).fill('browser_user');
    await page.getByLabel('Password', { exact: true }).fill('browser-test-password');
    await page.getByRole('button', { name: 'Create account', exact: true }).click();
    await page.getByLabel('Report title').fill('Browser fixture');
    await page.getByLabel('Policy text', { exact: true }).fill(policy);
    await page.getByText('Hybrid starts with local rules', { exact: false }).waitFor();
    assert.equal(await page.getByLabel('Review mode', { exact: true }).inputValue(), 'hybrid', 'Available Hybrid is the default.');
    await page.getByLabel('Review mode', { exact: true }).selectOption('rules_only');
    await page.getByLabel('Policy type', { exact: true }).selectOption('terms');
    assert.equal(await page.getByLabel('Review mode', { exact: true }).inputValue(), 'rules_only', 'Keep the user\'s explicit local-only choice.');
    await page.getByLabel('Policy type', { exact: true }).selectOption('privacy');
    await page.getByLabel('Review mode', { exact: true }).selectOption('hybrid');
    const consent = page.getByRole('checkbox', { name: /I agree to send selected clauses/ });
    assert.equal(await consent.isChecked(), false);
    assert.equal(await page.getByRole('button', { name: 'Analyze policy', exact: true }).isDisabled(), true);
    await consent.check();
    await page.getByLabel('Policy source', { exact: true }).selectOption('url');
    assert.equal(await consent.isChecked(), false, 'Changing source kind resets consent.');
    await page.getByLabel('Public policy or homepage URL', { exact: true }).fill('https://example.com/');
    await consent.check();
    await page.getByRole('button', { name: 'Find policy links', exact: true }).click();
    await page.getByRole('button', { name: 'Select Privacy policy', exact: true }).click();
    assert.equal(await page.getByLabel('Public policy or homepage URL', { exact: true }).inputValue(), 'https://example.com/privacy');
    assert.equal(await consent.isChecked(), false, 'Candidate selection resets consent.');
    assert.equal(submitted, false, 'Discovery and selection never enqueue analysis.');
    await page.getByLabel('Policy source', { exact: true }).selectOption('paste');
    await consent.check();
    await page.getByLabel('Policy text', { exact: true }).fill(policy + ' ');
    assert.equal(await consent.isChecked(), false, 'Changing the policy requires fresh consent.');
    await page.getByLabel('Policy text', { exact: true }).fill(policy);
    await consent.check();
    await page.getByRole('button', { name: 'Analyze policy', exact: true }).click();
    await page.getByRole('heading', { name: 'Browser fixture', exact: true }).waitFor();
    await page.getByRole('status').filter({ hasText: 'External review limits are not verified' }).waitFor();
    assert.equal(await page.getByRole('heading', { name: 'Analyze a policy', exact: true }).count(), 0, 'A saved report replaces the composer.');
    assert.ok((await page.locator('.report').boundingBox()).y < 300, 'Report starts in the first viewport.');
    if (process.env.TERMSLENS_UI_CAPTURE_DIR) {
      await fs.mkdir(process.env.TERMSLENS_UI_CAPTURE_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.TERMSLENS_UI_CAPTURE_DIR, 'mixed-desktop.png'), fullPage: true });
    }
    await page.locator('summary').filter({ hasText: /^Versions and reanalysis$/ }).click();
    const versionPanel = page.getByRole('region', { name: 'Versions and reanalysis' });
    assert.equal(await versionPanel.getByLabel('Review mode', { exact: true }).inputValue(), 'hybrid', 'Reanalysis defaults to Hybrid when available.');
    await versionPanel.getByRole('button', { name: 'Select version 1', exact: true }).click();
    assert.equal(await versionPanel.getByLabel('Document action', { exact: true }).inputValue(), 'reanalysis');
    await versionPanel.getByLabel('Review mode', { exact: true }).selectOption('hybrid');
    const versionConsent = versionPanel.getByRole('checkbox', { name: /I agree to send selected clauses/ });
    await versionConsent.check();
    await versionPanel.getByLabel('Document action', { exact: true }).selectOption('refresh');
    assert.equal(await versionConsent.isChecked(), false, 'Changing the document action requires fresh consent.');
    const readsBeforeViewChange = reportReads;
    await page.locator('summary').filter({ hasText: /^Score profile, review details/ }).click();
    await page.getByRole('button', { name: 'Show rules-only baseline', exact: true }).click();
    await page.getByRole('button', { name: 'Show saved result', exact: true }).click();
    assert.equal(reportReads, readsBeforeViewChange, 'Baseline switching uses the saved report without new calls.');
    const balancedScore = await page.locator('.score-panel .score').innerText();
    await page.getByLabel('Scoring profile', { exact: true }).selectOption('privacy_first');
    await page.getByText(/Privacy-first view/).waitFor();
    assert.notEqual(await page.locator('.score-panel .score').innerText(), balancedScore);
    await page.getByRole('button', { name: 'Show rules-only baseline', exact: true }).click();
    assert.equal(await page.getByLabel('Scoring profile', { exact: true }).inputValue(), 'privacy_first');
    await page.getByRole('button', { name: 'Show saved result', exact: true }).click();
    assert.equal(reportReads, readsBeforeViewChange, 'Profile and baseline switching make no additional requests.');
    await page.getByRole('button', { name: /Highlight exact evidence/ }).first().click();
    const quote = report.clauses.flatMap(c => c.findings)[0].quote;
    assert.equal(await page.locator('mark').innerText(), quote);
    assert.equal(await page.locator('mark').evaluate(node => document.activeElement === node), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.getByRole('dialog').count(), 0, 'Escape closes the evidence dialog.');
    assert.equal(await page.getByRole('button', { name: /Highlight exact evidence/ }).first().evaluate(node => node === document.activeElement), true, 'Evidence restores keyboard focus.');
    await page.locator('summary').filter({ hasText: /^Compare saved reports$/ }).click();
    await page.getByLabel('Compare this report with', { exact: true }).selectOption(other.id);
    await page.getByRole('button', { name: 'Compare reports', exact: true }).click();
    await page.getByText('Interpretation: changed findings or status', { exact: true }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Mobile layout must not overflow');
    await page.getByRole('button', { name: '← New analysis', exact: true }).click();
    await page.getByRole('heading', { name: 'Analyze a policy', exact: true }).waitFor();
    assert.equal(await page.getByRole('heading', { name: 'Browser fixture', exact: true }).count(), 0, 'New analysis removes the old report.');
    await page.locator('.history-link').filter({ hasText: 'Browser fixture' }).click();
    await page.getByRole('heading', { name: 'Browser fixture', exact: true }).waitFor();
    await page.locator('summary').filter({ hasText: /^Manage this document$/ }).click();
    await page.getByRole('button', { name: 'Delete all versions of this document', exact: true }).click();
    const cancel = page.getByRole('button', { name: 'Cancel deletion', exact: true });
    assert.equal(await cancel.evaluate(node => document.activeElement === node), true);
    assert.equal(deleted, false, 'Opening confirmation must not delete.');
    await cancel.click();
    assert.equal(deleted, false, 'Cancelling must not delete.');
    await page.getByRole('button', { name: 'Delete all versions of this document', exact: true }).click();
    await page.getByRole('button', { name: 'Confirm deletion', exact: true }).click();
    await page.getByRole('region', { name: 'Deletion status' }).getByText(/deleted/).waitFor();
    await page.getByRole('heading', { name: 'Browser fixture', exact: true }).waitFor({ state: 'detached' });
    assert.equal(await page.getByRole('heading', { name: 'Browser fixture', exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Log out', exact: true }).click();
    assert.equal(await page.getByRole('heading', { name: 'Browser fixture', exact: true }).count(), 0);
    failLogin = true;
    await page.getByLabel('Password', { exact: true }).fill('browser-test-password');
    await page.getByRole('button', { name: 'Log in', exact: true }).click();
    await page.getByRole('alert').filter({ hasText: 'Database unavailable in this test.' }).waitFor();
    failLogin = false; hybridAvailable = false;
    await page.getByRole('button', { name: 'Log in', exact: true }).click();
    await page.getByText('AI verification has expired.', { exact: false }).waitFor();
    assert.equal(await page.getByLabel('Review mode', { exact: true }).inputValue(), 'rules_only', 'Unavailable AI falls back to local-only selection.');
    assert.deepEqual(errors, []);
    console.log('PASS: mocked browser auth, Hybrid defaults/explicit choice/consent/fallback, report navigation/profiles, history, comparisons, deletion, Unicode dialog focus/Escape and mobile width.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
