import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, errorMessage } from './api';
import type { Clause, ComparisonHistory, ComparisonResult, History, ProfileId, Report } from './types';

function Passage({ clause }: { clause: Clause | null }) {
  if (!clause) return <p className="muted">No aligned passage.</p>;
  return <div><blockquote>{clause.text}</blockquote>
    {clause.findings.length ? <ul>{clause.findings.map((f, i) => <li key={i}>{f.rating}, severity {f.severity}/5 · category {f.category} · {f.source === 'ai' ? 'AI review' : 'Rule/library evidence'}</li>)}</ul>
      : <small>{clause.substantive ? 'Unrated: no supported finding.' : 'Heading or navigation.'}</small>}
  </div>;
}

export function ComparisonView({ data }: { data: ComparisonResult }) {
  return <div className="comparison-result">
    <h3>{data.kind === 'versions' ? 'Version comparison' : 'Policy comparison'} · {data.profile === 'privacy_first' ? 'Privacy-first' : 'Balanced'}</h3>
    <div className="comparison-columns">{[data.left, data.right].map((report, i) => <div key={i}>
      <h4>{i ? 'Right' : 'Left'}: {report.title}</h4><p>Version {report.version_number}, report revision {report.revision_number}</p>
      <p>{report.score.worst}–{report.score.best} / 100 · {Math.round(report.score.coverage * 100)}% coverage</p>
      <small>Requested: {report.requested_mode === 'hybrid' ? 'Hybrid' : 'Rules-only'} · Result: {report.effective_mode === 'hybrid' ? 'Hybrid' : 'Rules-only'}</small>
      {report.degradation_message && <p className="notice">{report.degradation_message}</p>}
    </div>)}</div>
    <p>{data.summary.source_text_changed ? 'The saved source texts differ; ordering or whitespace can also cause a difference.' : 'The saved source text is identical.'}</p>
    <p>{data.summary.text_changes} added, removed or modified passages. {data.summary.interpretation_changes} aligned passages have changed findings or unrated status.</p>
    <p className="muted">Interpretation changes are shown separately from policy text changes. Similar wording is a tentative match and does not establish the same meaning.</p>
    {data.limits.similarity_limited && <p className="notice">Similarity matching reached its limit. Remaining unmatched passages appear as added or removed; exact matches are retained.</p>}
    {data.passages.length ? data.passages.map((row, i) => <article className="comparison-passage" key={i}>
      <h4>Text: {row.text_change}{row.similarity !== null ? ' · tentative match' : ''}</h4>
      <p>Interpretation: {!row.left || !row.right ? 'no aligned passage to compare' : row.interpretation_changed ? 'changed findings or status' : 'no change detected in aligned findings'}</p>
      <div className="comparison-columns"><div aria-label="Left passage"><Passage clause={row.left} /></div><div aria-label="Right passage"><Passage clause={row.right} /></div></div>
    </article>) : <p>No passages match this view.</p>}
  </div>;
}

export default function ComparisonPanel({ report, csrf }: { report: Report; csrf: string }) {
  const [history, setHistory] = useState<History | null>(null);
  const [historyPage, setHistoryPage] = useState(0);
  const [saved, setSaved] = useState<ComparisonHistory | null>(null);
  const [savedPage, setSavedPage] = useState(0);
  const [savedRevision, setSavedRevision] = useState(0);
  const [rightId, setRightId] = useState('');
  const [profile, setProfile] = useState<ProfileId>('balanced');
  const [comparisonId, setComparisonId] = useState<string | null>(null);
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [page, setPage] = useState(0);
  const [changesOnly, setChangesOnly] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController(); setHistory(null); setRightId('');
    api<History>(`/analyses?limit=20&offset=${historyPage * 20}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setHistory(value); })
      .catch(err => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [report.id, historyPage]);
  useEffect(() => {
    const controller = new AbortController();
    api<ComparisonHistory>(`/comparisons?analysis_id=${report.id}&limit=10&offset=${savedPage * 10}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setSaved(value); })
      .catch(err => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [report.id, savedPage, savedRevision]);
  useEffect(() => {
    if (!comparisonId) return;
    const controller = new AbortController(); setComparison(null);
    api<ComparisonResult>(`/comparisons/${comparisonId}?limit=20&offset=${page * 20}&changes_only=${changesOnly}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setComparison(value); })
      .catch(err => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [comparisonId, page, changesOnly]);
  async function compare(event: FormEvent) {
    event.preventDefault();
    const right = history?.items.find(item => item.id === rightId);
    if (!right || busy) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError('');
    try {
      const result = await api<{ comparison_id: string }>('/comparisons', { method: 'POST', signal: controller.signal,
        body: JSON.stringify({ left_id: report.id, right_id: right.id, kind: right.document_id === report.document_id ? 'versions' : 'policies', profile }) }, csrf);
      if (!controller.signal.aborted) { setPage(0); setComparisonId(result.comparison_id); setSavedRevision(n => n + 1); }
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="card comparison-panel" aria-labelledby="compare-title">
    <h2 id="compare-title">Compare saved reports</h2>
    <p>Use two reports of the same policy type and matching review settings. If settings differ, reanalyze their saved captures first. Comparing makes no external-review calls.</p>
    {error && <p className="error" role="alert">{error}</p>}
    <form onSubmit={event => void compare(event)}>
      <label>Compare this report with<select required value={rightId} onChange={e => setRightId(e.target.value)}>
        <option value="">Select another saved report</option>{history?.items.filter(item => item.id !== report.id && item.document_type === report.document_type).map(item =>
          <option key={item.id} value={item.id}>{item.title} · v{item.version_number || 1}/r{item.revision_number || 1} · {item.effective_mode === 'hybrid' ? 'Hybrid' : 'Rules-only'}</option>)}
      </select></label>
      <label>Comparison scoring profile<select value={profile} onChange={e => setProfile(e.target.value as ProfileId)}><option value="balanced">Balanced</option><option value="privacy_first">Privacy-first</option></select></label>
      <button className="primary" disabled={busy || !rightId}>{busy ? 'Comparing…' : 'Compare reports'}</button>
    </form>
    {history && history.total > 20 && <nav className="pagination" aria-label="Comparison report choices"><button disabled={!historyPage} onClick={() => setHistoryPage(n => n - 1)}>Previous report choices</button><span>{historyPage + 1}</span><button disabled={(historyPage + 1) * 20 >= history.total} onClick={() => setHistoryPage(n => n + 1)}>Next report choices</button></nav>}
    {saved && saved.items.length > 0 && <><h3>Saved comparisons for this report</h3><ul>{saved.items.map(item => <li key={item.id}><button type="button" onClick={() => { setPage(0); setComparisonId(item.id); }}>{item.left_title} / {item.right_title} · {new Date(item.created_at).toLocaleString()}</button></li>)}</ul>
      {saved.total > 10 && <nav className="pagination" aria-label="Saved comparison pages"><button disabled={!savedPage} onClick={() => setSavedPage(n => n - 1)}>Previous comparisons</button><span>{savedPage + 1}</span><button disabled={(savedPage + 1) * 10 >= saved.total} onClick={() => setSavedPage(n => n + 1)}>Next comparisons</button></nav>}</>}
    {comparisonId && <label className="check"><input type="checkbox" checked={changesOnly} onChange={e => { setChangesOnly(e.target.checked); setPage(0); }} /> Show changed passages only</label>}
    {comparisonId && !comparison && <p role="status">Loading saved comparison…</p>}
    {comparison && <><ComparisonView data={comparison} />{comparison.total > 20 && <nav className="pagination" aria-label="Compared passages"><button disabled={!page} onClick={() => setPage(n => n - 1)}>Previous passages</button><span>{page + 1}</span><button disabled={(page + 1) * 20 >= comparison.total} onClick={() => setPage(n => n + 1)}>Next passages</button></nav>}</>}
  </section>;
}
