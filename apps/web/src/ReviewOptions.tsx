import { useId } from 'react';
import type { ReviewMode } from './types';

export default function ReviewOptions({ mode, consent, available, onMode, onConsent }: {
  mode: ReviewMode; consent: boolean; available: boolean;
  onMode: (value: ReviewMode) => void; onConsent: (value: boolean) => void;
}) {
  const hintId = useId();
  return <>
    <label>Review mode<select value={mode} onChange={e => onMode(e.target.value as ReviewMode)} aria-describedby={hintId}>
      <option value="rules_only">Rules-only</option><option value="hybrid" disabled={!available}>Hybrid: rules + AI review</option>
    </select></label>
    <small id={hintId}>{available ? 'Rules-only keeps your text local. Hybrid can send selected clauses and context to Groq.' : 'External AI review is unavailable. Rules-only keeps your text local.'}</small>
    {mode === 'hybrid' && <label className="check"><input type="checkbox" required checked={consent} onChange={e => onConsent(e.target.checked)} />
      I agree to send selected clauses and their context from this policy to Groq for external AI review.
    </label>}
  </>;
}
