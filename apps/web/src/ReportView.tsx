import { useEffect, useRef, useState } from 'react';
import { evidenceSlices } from './evidence';
import { selectScoreProfile } from './profile';
import type { Finding, ProfileId, Report } from './types';

const reason: Record<string, string> = {
  unsupported_wording: 'This wording needs further interpretation.',
  context_requires_review: 'Definitions or exceptions require contextual review.',
  conflicting_statements: 'Statements conflict; no rating was assigned.',
  model_uncertain: 'The external reviewer could not support a reliable interpretation.',
  analysis_budget_excluded: 'This clause was outside the bounded external-review budget.',
  adequate_context_does_not_fit: 'The necessary context could not fit without cutting information.',
  invalid_response_schema: 'The external response could not be validated.',
  evidence_not_found: 'The external response did not provide matching source evidence.',
  duplicate_or_conflicting_findings: 'The external findings conflicted; no rating was assigned.',
  no_valid_finding: 'No valid external finding was returned for this clause.',
};
const modeLabel = (mode: string) => mode === 'hybrid' ? 'Hybrid' : 'Rules-only';
const sourceLabel = { rule: 'Rule match', library_exact: 'Reviewed exact match', ai: 'AI review' };

export default function ReportView({ report }: { report: Report }) {
  const [category, setCategory] = useState('all');
  const [rating, setRating] = useState('all');
  const [source, setSource] = useState('all');
  const [baselineView, setBaselineView] = useState(false);
  const [profileId, setProfileId] = useState<ProfileId>('balanced');
  const [showUnrated, setShowUnrated] = useState(true);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Finding | null>(null);
  const mark = useRef<HTMLElement>(null);
  const shown = baselineView && report.rules_baseline ? report.rules_baseline : report;
  const profile = selectScoreProfile(shown, profileId);
  const slices = selected ? evidenceSlices(report.text, selected.start, selected.end, selected.quote) : null;
  useEffect(() => { if (selected && slices) mark.current?.focus(); }, [selected]);
  useEffect(() => { setPage(0); }, [category, rating, source, showUnrated]);
  const matches = (f: Finding) => (category === 'all' || f.category === Number(category)) && (rating === 'all' || f.rating === rating) && (source === 'all' || f.source === source);
  const visible = shown.clauses.filter(clause => clause.substantive && (clause.findings.length
    ? clause.findings.some(matches)
    : showUnrated && category === 'all' && rating === 'all' && source === 'all'));
  const pages = Math.max(1, Math.ceil(visible.length / 20));
  const currentPage = Math.min(page, pages - 1);
  const unknown = shown.clauses.filter(c => c.substantive && !c.findings.length).length;

  return <section className="report" aria-labelledby="report-title">
    <div className="section-heading"><div><p className="eyebrow">Saved analysis · {report.document_type}</p><h2 id="report-title">{report.title}</h2></div><span className="badge">{baselineView ? 'Rules-only baseline' : modeLabel(report.effective_mode)}</span></div>
    <p className="muted">Captured {new Date(report.captured_at).toLocaleString()} · {profile.label} view</p>
    {report.source_kind === 'url' && report.capture && <details className="capture-source"><summary>Captured page details</summary>
      <dl><dt>Requested URL</dt><dd>{report.capture.source_url}</dd><dt>Final URL</dt><dd>{report.capture.final_url}</dd>
        <dt>Format</dt><dd>{report.capture.content_type}</dd><dt>Extractor</dt><dd>{report.engine.extractor}</dd>
        <dt>Saved text SHA-256</dt><dd>{report.capture.text_sha256}</dd></dl>
      <p>The report uses the saved static text. The page may have changed since capture.</p>
    </details>}
    <label className="profile-control">Scoring profile<select value={profile.id} aria-describedby="profile-help" onChange={e => setProfileId(e.target.value as ProfileId)}>
      <option value="balanced">Balanced</option><option value="privacy_first" disabled={!shown.score_profiles?.privacy_first}>Privacy-first</option>
    </select></label>
    <p id="profile-help" className="muted">{shown.score_profiles?.privacy_first
      ? 'Balanced weights all in-scope categories equally. Privacy-first gives more weight to data sharing, retention, tracking and content use. Both use the same saved evidence.'
      : 'This saved report supports the balanced view only.'}</p>
    <p>Requested: {modeLabel(report.requested_mode)} · Saved result: {modeLabel(report.effective_mode)}</p>
    {report.degradation_reason && <p className="notice" role="status">{report.degradation_message || 'External review was unavailable. This report uses local rules.'}</p>}
    {report.consent_provider && report.consent_at && <p className="muted">External-review consent: {report.consent_provider === 'groq' ? 'Groq' : report.consent_provider}, {new Date(report.consent_at).toLocaleString()}.</p>}
    {report.requested_mode === 'hybrid' && <p className="muted">{report.accepted_ai_findings || 0} accepted AI findings. Invalid or uncertain responses remain unrated.</p>}
    {report.rules_baseline ? <button type="button" aria-pressed={baselineView} onClick={() => { setBaselineView(v => !v); setSelected(null); setSource('all'); setPage(0); }}>
      {baselineView ? 'Show saved result' : 'Show rules-only baseline'}
    </button> : <p className="muted">A separate rules baseline was not stored with this earlier report.</p>}
    <div className="summary-grid" aria-live="polite" aria-atomic="true">
      <div><span className="label">Score range</span><strong className="score">{profile.score.worst}–{profile.score.best}<small> / 100</small></strong><span>{profile.score.grade ? `Grade ${profile.score.grade}` : 'Insufficient evidence for one grade'}</span></div>
      <div><span className="label">Clause coverage</span><strong className="score">{Math.round(profile.score.coverage * 100)}<small>%</small></strong><span>{unknown} substantive {unknown === 1 ? 'clause remains' : 'clauses remain'} unrated</span></div>
    </div>
    <p className="notice">This report supports selected patterns in {shown.supported_groups.length} clause groups: {shown.categories.filter(c => shown.supported_groups.includes(c.id)).map(c => c.name).join(', ')}. Missing or unfamiliar evidence remains unknown. A higher score is more favorable; the range reflects uncertainty.</p>
    {!profile.score.scope_consistent && <p className="error" role="alert">Some findings fall outside the selected policy type. A single grade is withheld.</p>}
    <div className="categories" aria-label="Category findings">
      {shown.categories.map(c => <div className={`category ${c.applicable ? '' : 'outside'}`} key={c.id}>
        <div><strong>{c.name}</strong><span>{!c.applicable ? 'Outside selected scope' : c.accepted_count ? `Observed severity ${c.max_severity}/5${c.unknown ? ' · incomplete evidence' : ''}` : 'Unknown · no rated evidence'}</span></div>
        {c.applicable && profile.weights[c.id] !== undefined && <span>Profile weight: {Number((profile.weights[c.id] * 100).toFixed(1))}%</span>}
        {c.applicable && c.accepted_count > 0 && <meter min="0" max="5" value={c.max_severity} aria-label={`${c.name}: observed severity ${c.max_severity} out of 5`} />}
      </div>)}
    </div>
    <h3>Clauses and evidence</h3>
    <div className="filters">
      <label>Category<select value={category} onChange={e => setCategory(e.target.value)}><option value="all">All categories</option>{report.categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>Rating<select value={rating} onChange={e => setRating(e.target.value)}><option value="all">All ratings</option>{['favorable', 'neutral', 'concerning', 'invasive'].map(r => <option key={r} value={r}>{r}</option>)}</select></label>
      <label>Finding source<select value={source} onChange={e => setSource(e.target.value)}><option value="all">All sources</option><option value="rule">Rule match</option><option value="library_exact">Reviewed exact match</option><option value="ai" disabled={baselineView}>AI review</option></select></label>
      <label className="check"><input type="checkbox" checked={showUnrated} onChange={e => setShowUnrated(e.target.checked)} /> Include unrated clauses</label>
    </div>
    <p className="muted" aria-live="polite">{visible.length} matching clauses</p>
    <div className="evidence-layout">
      <div>
        {visible.slice(currentPage * 20, (currentPage + 1) * 20).map(clause => <article className="clause" key={clause.id}>
          <h4>Clause {clause.id}</h4><blockquote>{clause.text}</blockquote>
          {clause.findings.filter(matches).map(f => <div className="finding" key={f.rule_id}>
            <p><span className={`rating ${f.rating}`}>{f.rating} · {f.severity}/5</span> <span className="muted">{sourceLabel[f.source]}</span></p>
            {f.source === 'ai' && <p className="muted">Model: {report.ai_provenance?.find(p => p.id === f.ai_call_id)?.model || 'Not recorded'}</p>}
            <p>{f.explanation}</p><button className="text-button" onClick={() => setSelected(f)}>Highlight exact evidence for clause {clause.id}</button>
          </div>)}
          {!clause.findings.length && <p className="muted"><strong>Unrated.</strong> {reason[clause.unrated_reason || ''] || 'No supported interpretation is available.'}</p>}
        </article>)}
        {!visible.length && <p>No clauses match these filters.</p>}
        {pages > 1 && <nav className="pagination" aria-label="Clause pages"><button disabled={!currentPage} onClick={() => setPage(p => p - 1)}>Previous clauses</button><span>{currentPage + 1} / {pages}</span><button disabled={currentPage + 1 >= pages} onClick={() => setPage(p => p + 1)}>Next clauses</button></nav>}
      </div>
      <section className="source" aria-labelledby="source-title"><h4 id="source-title">Saved source text</h4><p className="muted">Select a finding to highlight its exact quote.</p>
        {selected && !slices && <p className="error" role="alert">This quote could not be matched. No highlight is shown.</p>}
        <pre>{slices ? <>{slices.before}<mark ref={mark} tabIndex={-1} aria-label="Selected evidence">{slices.quote}</mark>{slices.after}</> : report.text}</pre>
      </section>
    </div>
  </section>;
}
