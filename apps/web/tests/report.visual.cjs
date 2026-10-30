// Read-only browser replay of a local report; no real API or provider calls.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.TERMSLENS_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../../..');
const dist = path.join(root, 'apps/web/dist');

(async () => {
  const report = JSON.parse(await fs.readFile(process.env.TERMSLENS_REPORT_FIXTURE || path.join(root, 'var/ui-review/latest-report.json'), 'utf8'));
  const output = path.resolve(root, process.env.TERMSLENS_UI_CAPTURE_DIR || 'var/ui-review/current');
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, timeout: 15000 });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => sessionStorage.setItem('termslens.job.visual-user', 'visual-job'));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      assert.equal(url.origin, 'http://127.0.0.1:4173', 'Preview must not contact external services');
      assert.equal(route.request().method(), 'GET', 'Preview must not submit or mutate');
      const json = data => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
      const p = url.pathname;
      if (p === '/api/me') return json({ user: { id: 'visual-user', username: 'Demo' }, csrf_token: 'visual-only' });
      if (p === '/api/health') return json({ external_review: { available: true } });
      if (p === '/api/jobs/visual-job') return json({ id: 'visual-job', state: 'done', stage: 'complete', result_id: report.id });
      if (p === `/api/analyses/${report.id}`) return json(report);
      if (p === '/api/analyses') return json({ items: [{ ...report, ...report.score }], total: 1 });
      if (p.endsWith('/versions')) return json({ items: [], total: 0, last_check: null });
      if (p.startsWith('/api/')) return json({ items: [], total: 0 });
      const file = path.resolve(dist, p === '/' ? 'index.html' : '.' + p);
      assert.ok(file.startsWith(dist + path.sep));
      return route.fulfill({ body: await fs.readFile(file), contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' });
    });
    await page.goto('http://127.0.0.1:4173/');
    await page.getByRole('heading', { name: report.title, exact: true }).waitFor();
    await page.screenshot({ path: path.join(output, 'desktop.png'), fullPage: true });
    await page.screenshot({ path: path.join(output, 'desktop-viewport.png') });
    const reportTop = await page.locator('.report').evaluate(el => Math.round(el.getBoundingClientRect().top));
    assert.ok(reportTop < 300, 'Saved report should start in the first viewport');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(output, 'mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'Mobile must not overflow horizontally');
    const favorable = report.clauses.filter(c => c.substantive && c.findings.some(f => f.rating === 'favorable'));
    if (favorable.length) {
      await page.getByRole('button', { name: 'View all favorable findings', exact: true }).click();
      assert.equal(await page.locator('.clause-browser .clause').count(), Math.min(8, favorable.length));
      await page.getByRole('button', { name: /Highlight exact evidence/ }).first().click();
      assert.equal(await page.getByRole('dialog').isVisible(), true);
      assert.equal(await page.locator('mark').evaluate(node => node === document.activeElement), true);
      await page.keyboard.press('Escape');
    }
    await page.getByRole('button', { name: 'Review unresolved clauses', exact: true }).click();
    const unresolved = report.clauses.filter(c => c.substantive && !c.findings.length);
    assert.equal(await page.locator('.clause-browser .clause').count(), Math.min(8, unresolved.length));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ report_top_at_desktop: reportTop, mobile_no_overflow: true, browser_errors: 0, screenshots: output }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
