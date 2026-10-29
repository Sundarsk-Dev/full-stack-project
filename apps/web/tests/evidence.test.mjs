import test from 'node:test';
import assert from 'node:assert/strict';
import { evidenceSlices } from '../src/evidence.ts';

test('code-point offsets highlight an exact quote after non-BMP text', () => {
  const text = 'A 📜 policy. We sell your personal data.';
  const quote = 'We sell your personal data.';
  const start = Array.from('A 📜 policy. ').length;
  const parts = evidenceSlices(text, start, Array.from(text).length, quote);
  assert.equal(parts.quote, quote);
  assert.equal(parts.before + parts.quote + parts.after, text);
});

test('invalid or mismatched evidence is never highlighted', () => {
  for (const [start, end, quote] of [[-1, 4, 'text'], [0, 100, 'text'], [1.5, 4, 'text'], [0, 4, 'fake'], [2, 1, '']]) {
    assert.equal(evidenceSlices('text', start, end, quote), null);
  }
});
