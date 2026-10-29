import type { Baseline, ProfileId } from './types';

export function selectScoreProfile(view: Baseline, requested: ProfileId) {
  const id = requested === 'privacy_first' && view.score_profiles?.privacy_first ? 'privacy_first' : 'balanced';
  const profile = view.score_profiles?.[id];
  return { id, label: id === 'privacy_first' ? 'Privacy-first' : 'Balanced',
    score: profile?.score ?? view.score, weights: profile?.weights ?? {} };
}
