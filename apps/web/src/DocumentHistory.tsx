import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from './api';
import ReviewOptions from './ReviewOptions';
import type { Report, ReviewMode, RevisionHistory, SavedVersion, VersionHistory } from './types';

export function VersionList({ history, onSelect, onOpen }: {
  history: VersionHistory; onSelect: (version: SavedVersion) => void; onOpen: (id: string) => void;
}) {
  return <ul className="version-list">{history.items.map(version => <li key={version.id}>
    <strong>Version {version.version_number}</strong> <span>Captured {new Date(version.captured_at).toLocaleString()}</span>
    <small>{version.analysis_count} saved report {version.analysis_count === 1 ? 'revision' : 'revisions'}</small>
    <div><button type="button" onClick={() => onSelect(version)}>Select version {version.version_number}</button>{' '}
      {version.latest_analysis_id && <button type="button" onClick={() => onOpen(version.latest_analysis_id!)}>Open latest report for version {version.version_number}</button>}</div>
  </li>)}</ul>;
}

export default function DocumentHistory({ report, csrf, hybridAvailable, hybridReason, activeJob, onJob, onOpen }: {
  report: Report; csrf: string; hybridAvailable: boolean; activeJob: boolean; onJob: (id: string) => void; onOpen: (id: string) => void;
  hybridReason?: string;
}) {
  const [history, setHistory] = useState<VersionHistory | null>(null);
  const [revisions, setRevisions] = useState<RevisionHistory | null>(null);
  const [page, setPage] = useState(0);
  const [revisionPage, setRevisionPage] = useState(0);
  const [selected, setSelected] = useState({ id: report.version_id, number: report.version_number || 1 });
  const [action, setAction] = useState<'refresh' | 'reanalysis'>('refresh');
  const [text, setText] = useState(report.text);
  const [modeChoice, setMode] = useState<ReviewMode | null>(null);
  const mode: ReviewMode = hybridAvailable ? modeChoice || 'hybrid' : 'rules_only';
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | null>(null);
  const submission = useRef<{ identity: string; key: string } | null>(null);
  const paste = report.source_kind !== 'url';
  const words = text.trim() ? text.trim().split(/\s+/u).length : 0;
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => { setConsent(false); }, [hybridAvailable]);
  useEffect(() => {
    const controller = new AbortController(); setHistory(null);
    api<VersionHistory>(`/documents/${report.document_id}/versions?limit=10&offset=${page * 10}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setHistory(value); })
      .catch(err => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [report.document_id, page]);
  useEffect(() => {
    const controller = new AbortController(); setRevisions(null);
    api<RevisionHistory>(`/documents/${report.document_id}/versions/${selected.id}/analyses?limit=10&offset=${revisionPage * 10}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setRevisions(value); })
      .catch(err => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [report.document_id, selected.id, revisionPage]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || activeJob || (mode === 'hybrid' && (!hybridAvailable || !consent))) return;
    if (action === 'refresh' && paste && (!text.trim() || words > 25000)) return;
    const path = action === 'refresh' ? `/documents/${report.document_id}/refresh` : '/analyses';
    const body = JSON.stringify({ ...(action === 'refresh' ? (paste ? { text } : {}) : { version_id: selected.id, document_type: report.document_type }),
      mode, ...(mode === 'hybrid' ? { consent: true, provider: 'groq' } : {}) });
    const identity = path + body;
    if (submission.current?.identity !== identity) submission.current = { identity, key: crypto.randomUUID() };
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError('');
    try {
      const created = await api<{ job_id: string }>(path, { method: 'POST', body, headers: { 'Idempotency-Key': submission.current.key }, signal: controller.signal }, csrf);
      if (!controller.signal.aborted) { setConsent(false); onJob(created.job_id); }
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="card document-history" aria-labelledby="versions-title">
    <h2 id="versions-title">Versions and reanalysis</h2>
    <p>This report uses version {report.version_number || 1}, report revision {report.revision_number || 1}. Earlier captures and reports stay available.</p>
    {error && <p className="error" role="alert">{error}</p>}
    {history ? <>
      {history.last_check && <p>Last source check: {new Date(history.last_check.checked_at).toLocaleString()}. {history.last_check.changed ? 'A capture was saved.' : 'The captured content was unchanged.'}</p>}
      <VersionList history={history} onOpen={onOpen} onSelect={version => { setSelected({ id: version.id, number: version.version_number }); setRevisionPage(0); setAction('reanalysis'); setConsent(false); }} />
      {history.total > 10 && <nav className="pagination" aria-label="Version pages"><button disabled={!page} onClick={() => setPage(n => n - 1)}>Previous versions</button><span>{page + 1}</span><button disabled={(page + 1) * 10 >= history.total} onClick={() => setPage(n => n + 1)}>Next versions</button></nav>}
    </> : <p role="status">Loading versions…</p>}
    <h3>Reports for version {selected.number}</h3>
    {revisions ? <><ul className="version-list">{revisions.items.map(revision => <li key={revision.id}>
      <button type="button" onClick={() => onOpen(revision.id)}>Open report revision {revision.revision_number}</button>{' '}
      <span>{revision.effective_mode === 'hybrid' ? 'Hybrid' : 'Rules-only'}{revision.degradation_reason ? ' (external review unavailable)' : ''} · {new Date(revision.created_at).toLocaleString()}</span>
    </li>)}</ul>{revisions.total > 10 && <nav className="pagination" aria-label="Report revision pages"><button disabled={!revisionPage} onClick={() => setRevisionPage(n => n - 1)}>Previous revisions</button><span>{revisionPage + 1}</span><button disabled={(revisionPage + 1) * 10 >= revisions.total} onClick={() => setRevisionPage(n => n + 1)}>Next revisions</button></nav>}</> : <p role="status">Loading revisions…</p>}
    <form onSubmit={event => void submit(event)}>
      <label>Document action<select aria-label="Document action" value={action} onChange={e => { setAction(e.target.value as 'refresh' | 'reanalysis'); setConsent(false); }}>
        <option value="refresh">{paste ? 'Replace pasted text' : 'Check URL for changes'}</option>
        <option value="reanalysis">Reanalyze saved version {selected.number}</option>
      </select></label>
      <p className="muted">{action === 'refresh' ? 'Checks the current document. Changed text or context creates a version; unchanged content can reuse a compatible report.' : 'Uses the selected saved capture without fetching. Creates a new report revision with current rules and your selected review mode.'}</p>
      {action === 'refresh' && paste && <><label>Replacement policy text<textarea aria-label="Replacement policy text" rows={7} required maxLength={200000} value={text} onChange={e => { setText(e.target.value); setConsent(false); }} /></label><small>{words.toLocaleString()} / 25,000 words. Starts with this report's text; review it before replacing the current document.</small></>}
      <ReviewOptions mode={mode} consent={consent} available={hybridAvailable} unavailableReason={hybridReason} onMode={value => { setMode(value); setConsent(false); }} onConsent={setConsent} />
      <button className="primary" disabled={busy || activeJob || (action === 'refresh' && paste && (!text.trim() || words > 25000)) || (mode === 'hybrid' && (!hybridAvailable || !consent))}>
        {busy ? 'Submitting…' : action === 'refresh' ? 'Check and analyze changes' : `Reanalyze version ${selected.number}`}
      </button>
    </form>
  </section>;
}
