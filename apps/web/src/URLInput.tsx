import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { DiscoveryResult, PolicyCandidate } from './types';

export function CandidateList({ result, onSelect }: { result: DiscoveryResult; onSelect: (candidate: PolicyCandidate) => void }) {
  return <div aria-live="polite">
    <p>Select one policy, check its type, then choose Analyze policy.</p>
    {result.candidates.length ? <ul className="policy-candidates">{result.candidates.map(candidate => <li key={candidate.url}>
      <button type="button" onClick={() => onSelect(candidate)}>Select {candidate.title || candidate.document_type}</button>
      <span>{candidate.url}</span><small>Suggested type: {candidate.document_type}</small>
    </li>)}</ul> : <p>No policy links found. Enter a direct policy URL or paste its text.</p>}
    <small>Discovery reads static links from this site only. {result.limitations.includes('link_scan_limited') ? 'Only part of the link list could be scanned.' : ''}</small>
  </div>;
}

export default function URLInput({ value, csrf, onChange, onSelect, onPending }: {
  value: string; csrf: string; onChange: (value: string) => void;
  onSelect: (candidate: PolicyCandidate) => void; onPending: (value: boolean) => void;
}) {
  const [result, setResult] = useState<DiscoveryResult | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const current = useRef<AbortController | null>(null);
  function cancel() {
    current.current?.abort(); current.current = null;
    setPending(false); onPending(false);
  }
  useEffect(() => () => { current.current?.abort(); onPending(false); }, [onPending]);

  async function discover() {
    cancel(); setError(''); setResult(null);
    const controller = new AbortController(); current.current = controller;
    setPending(true); onPending(true);
    try {
      const found = await api<DiscoveryResult>('/discovery', { method: 'POST', body: JSON.stringify({ url: value }), signal: controller.signal }, csrf, 65000);
      if (!controller.signal.aborted) setResult(found);
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    finally { if (current.current === controller) { current.current = null; setPending(false); onPending(false); } }
  }
  return <div className="url-input">
    <label>Public policy or homepage URL<input type="url" required maxLength={2048} value={value} placeholder="https://example.com/privacy"
      onChange={e => { cancel(); setResult(null); setError(''); onChange(e.target.value); }} aria-describedby="url-hint" /></label>
    <p id="url-hint" className="muted">Analyze one English policy page. For a homepage, find its policy links and select one first. If the page is blocked or needs JavaScript, paste its text.</p>
    <button type="button" disabled={pending || !value.trim()} onClick={() => void discover()}>Find policy links</button>
    {pending && <><p role="status">Finding policy links; this can take up to a minute.</p><button type="button" onClick={cancel}>Cancel discovery</button></>}
    {error && <p className="error" role="alert">{error}</p>}
    {result && <CandidateList result={result} onSelect={candidate => { cancel(); setResult(null); onSelect(candidate); }} />}
  </div>;
}
