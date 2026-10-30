import { useId } from 'react';
import type { ReviewMode } from './types';

const unavailableMessages: Record<string, string> = {
  verification_expired_or_invalid: 'AI verification has expired. Renew the local readiness record, then refresh this page.',
  disabled: 'AI is switched off in the API process. Enable Groq and restart the API and worker.',
  readiness_missing_or_invalid: 'AI setup is incomplete or its readiness record is invalid. Complete the demo setup.',
  verification_incomplete: 'AI readiness checks are incomplete. Complete the demo setup.',
  key_missing_or_invalid: 'The API cannot use the saved Groq key. Check the private configuration.',
  model_missing: 'No Groq model is configured in the API.',
  model_mismatch: 'The configured model does not match the verified model.',
  api_unreachable: 'The API could not be reached to check AI availability. Start the API and refresh this page.',
};

export default function ReviewOptions({ mode, consent, available, unavailableReason, onMode, onConsent }: {
  mode: ReviewMode; consent: boolean; available: boolean;
  unavailableReason?: string;
  onMode: (value: ReviewMode) => void; onConsent: (value: boolean) => void;
}) {
  const hintId = useId();
  return <>
    <label>Review mode<select aria-label="Review mode" value={mode} onChange={e => onMode(e.target.value as ReviewMode)} aria-describedby={hintId}>
      <option value="hybrid" disabled={!available}>Hybrid: rules + AI review</option><option value="rules_only">Rules-only</option>
    </select></label>
    <small id={hintId}>{available ? 'Hybrid starts with local rules and uses AI for unresolved wording. Consent is required before anything is sent to Groq. You can choose Rules-only to keep text local.'
      : `${unavailableMessages[unavailableReason || ''] || 'External AI review is unavailable.'} Rules-only keeps your text local.`}</small>
    {mode === 'hybrid' && <label className="check"><input type="checkbox" required checked={consent} onChange={e => onConsent(e.target.checked)} />
      I agree to send selected clauses and their context from this policy to Groq for external AI review.
    </label>}
  </>;
}
