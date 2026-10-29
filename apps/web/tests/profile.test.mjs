import assert from 'node:assert/strict';
import test from 'node:test';
import { selectScoreProfile } from '../src/profile.ts';

const balanced = { best: 80, worst: 40, coverage: .8, grade: null, scope_consistent: true };
const privacy = { ...balanced, best: 65, worst: 25 };
const view = { score: balanced, score_profiles: { revision: 'fixture-revision',
  balanced: { score: balanced, weights: { 1: .125 } }, privacy_first: { score: privacy, weights: { 1: .25 } } } };

test('profile switching selects server-provided scores and weights without altering saved evidence', () => {
  const original = JSON.stringify(view);
  const oldFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error('Profile switching must remain local'); };
  try {
    const selected = selectScoreProfile(view, 'privacy_first');
    assert.equal(selected.id, 'privacy_first');
    assert.equal(selected.label, 'Privacy-first');
    assert.deepEqual(selected.score, privacy);
    assert.equal(selected.weights[1], .25);
    assert.deepEqual(selectScoreProfile(view, 'balanced').score, balanced);
    assert.equal(JSON.stringify(view), original);
  } finally { globalThis.fetch = oldFetch; }
});

test('switching to a baseline uses that baseline’s profile scores', () => {
  const baselineScore = { ...balanced, best: 100, worst: 0, coverage: 0 };
  const baseline = { ...view, score_profiles: { ...view.score_profiles, privacy_first: { score: baselineScore, weights: { 1: .25 } } } };
  assert.equal(selectScoreProfile(view, 'privacy_first').score.best, 65);
  assert.equal(selectScoreProfile(baseline, 'privacy_first').score.best, 100);
  assert.equal(selectScoreProfile(baseline, 'privacy_first').score.coverage, 0);
});

test('legacy reports fall back to their saved balanced score', () => {
  const selected = selectScoreProfile({ score: balanced }, 'privacy_first');
  assert.equal(selected.id, 'balanced');
  assert.equal(selected.label, 'Balanced');
  assert.deepEqual(selected.score, balanced);
  assert.deepEqual(selected.weights, {});
});
