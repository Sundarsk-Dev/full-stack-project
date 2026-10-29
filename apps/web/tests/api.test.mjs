import test from 'node:test';
import assert from 'node:assert/strict';
import { api, errorMessage } from '../src/api.ts';

test('failed responses preserve the server request reference for debugging', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'Database unavailable.' }), {
    status: 503, headers: { 'Content-Type': 'application/json', 'X-Request-ID': '12345678-1234-1234-1234-123456789abc' },
  });
  try {
    await assert.rejects(api('/analyses'), error => {
      assert.equal(error.status, 503);
      assert.match(errorMessage(error), /Reference: 12345678-1234-1234-1234-123456789abc/);
      return true;
    });
  } finally { globalThis.fetch = original; }
});

test('discovery allows a bounded minute and forwards explicit cancellation', async () => {
  const original = { fetch: globalThis.fetch, setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  const caller = new AbortController();
  let duration, cleared = false;
  globalThis.setTimeout = (_fn, ms) => { duration = ms; return 123; };
  globalThis.clearTimeout = id => { assert.equal(id, 123); cleared = true; };
  globalThis.fetch = async (_url, options) => new Promise((_resolve, reject) => {
    assert.equal(options.headers.get('X-CSRF-Token'), 'session-token');
    options.signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')));
    caller.abort();
  });
  try {
    await assert.rejects(api('/discovery', { method: 'POST', signal: caller.signal }, 'session-token', 65000), { name: 'AbortError' });
    assert.equal(duration, 65000);
    assert.equal(cleared, true);
    await assert.rejects(api('/discovery', {}, undefined, 65001), /Invalid request timeout/);
  } finally { Object.assign(globalThis, original); }
});
