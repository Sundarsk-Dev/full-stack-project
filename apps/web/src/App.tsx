import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, ApiError, errorMessage } from './api';
import ReportView from './ReportView';
import ReviewOptions from './ReviewOptions';
import URLInput from './URLInput';
import DocumentHistory from './DocumentHistory';
import ComparisonPanel from './ComparisonPanel';
import DocumentDelete, { DeletionStatus } from './DocumentDelete';
import type { Auth, DocumentType, History, Job, Report, ReviewMode } from './types';

const stages = ['queued', 'capturing', 'segmenting', 'analyzing', 'publishing', 'complete'];
const labels: Record<string, string> = { queued: 'Queued', capturing: 'Capturing policy', segmenting: 'Finding clauses', analyzing: 'Reviewing policy', publishing: 'Saving report', complete: 'Complete', failed: 'Failed' };
const example = 'Privacy policy\n\nWe sell your personal data to advertisers.\nWe retain your personal data indefinitely.\nWe use advertising cookies without your consent.\n\nYou can request a copy of your information.';
const jobKey = (id: string) => `termslens.job.${id}`;
const readSavedJob = (id: string) => { try { return sessionStorage.getItem(jobKey(id)); } catch { return null; } };
const saveJob = (id: string, value: string | null) => { try { if (value) sessionStorage.setItem(jobKey(id), value); else sessionStorage.removeItem(jobKey(id)); } catch { /* Storage is optional; polling still works. */ } };

export default function App() {
  const [auth, setAuth] = useState<Auth | null>(null);
  const [starting, setStarting] = useState(true);
  const [register, setRegister] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [inputKind, setInputKind] = useState<'paste' | 'url'>('paste');
  const [url, setUrl] = useState('');
  const [discovering, setDiscovering] = useState(false);
  const [documentType, setDocumentType] = useState<DocumentType>('privacy');
  const [modeChoice, setMode] = useState<ReviewMode | null>(null);
  const [consent, setConsent] = useState(false);
  const [hybridAvailable, setHybridAvailable] = useState(false);
  const [hybridReason, setHybridReason] = useState<string>();
  const mode: ReviewMode = hybridAvailable ? modeChoice || 'hybrid' : 'rules_only';
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [pollRevision, setPollRevision] = useState(0);
  const [reportId, setReportId] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [history, setHistory] = useState<History | null>(null);
  const [historyPage, setHistoryPage] = useState(0);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [deletionRevision, setDeletionRevision] = useState(0);
  const submission = useRef<{ body: string; key: string } | null>(null);
  const words = text.trim() ? text.trim().split(/\s+/u).length : 0;
  const inputReady = inputKind === 'paste' ? Boolean(text.trim()) && words <= 25000 : Boolean(url.trim());

  function acceptAuth(value: Auth) {
    setAuth(value); setRegister(false); setPassword(''); setError(''); setHistory(null); setHistoryPage(0);
    setReport(null); setReportId(null); setJob(null); setJobId(readSavedJob(value.user.id));
    setMode(null); setConsent(false); setHybridAvailable(false); setHybridReason(undefined); submission.current = null;
    setInputKind('paste'); setUrl(''); setDiscovering(false);
  }

  useEffect(() => {
    const controller = new AbortController();
    api<Auth>('/me', { signal: controller.signal }).then(value => { if (!controller.signal.aborted) acceptAuth(value); }).catch(err => {
      if (!controller.signal.aborted && !(err instanceof ApiError && err.status === 401)) setError(errorMessage(err));
    }).finally(() => { if (!controller.signal.aborted) setStarting(false); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!auth) return;
    const controller = new AbortController();
    api<{ external_review?: { available: boolean; reason?: string } }>('/health', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) { setHybridAvailable(value.external_review?.available === true); setHybridReason(value.external_review?.reason); } })
      .catch(() => { if (!controller.signal.aborted) { setHybridAvailable(false); setHybridReason('api_unreachable'); } });
    return () => controller.abort();
  }, [auth?.user.id]);

  useEffect(() => { setConsent(false); }, [hybridAvailable]);
  useEffect(() => {
    if (report) {
      document.getElementById('report-title')?.focus({ preventScroll: true });
      window.scrollTo({ top: 0 });
    }
  }, [report?.id]);

  useEffect(() => {
    if (!auth) return;
    const controller = new AbortController();
    api<History>(`/analyses?limit=10&offset=${historyPage * 10}`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setHistory(value); })
      .catch(err => { if (!controller.signal.aborted) setError(errorMessage(err)); });
    return () => controller.abort();
  }, [auth?.user.id, historyPage, historyRevision]);

  useEffect(() => {
    if (!auth || !jobId) return;
    const owner = auth.user.id;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<Job>(`/jobs/${jobId}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setJob(next);
        if (next.state === 'done' || next.state === 'partial') {
          saveJob(owner, null); setReportId(next.result_id); setHistoryRevision(n => n + 1);
        } else if (next.state === 'failed') {
          saveJob(owner, null); setError(next.error || 'Analysis failed. You can submit again.');
        } else timer = setTimeout(poll, 1000);
      } catch (err) {
        if (!controller.signal.aborted) setError(`${errorMessage(err)} Reload to resume checking this job.`);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [auth?.user.id, jobId, pollRevision]);

  useEffect(() => {
    if (!auth || !reportId) return;
    const controller = new AbortController();
    setReportLoading(true); setReport(null);
    api<Report>(`/analyses/${reportId}`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setReport(value);
    }).catch(err => { if (!controller.signal.aborted) setError(errorMessage(err)); })
      .finally(() => { if (!controller.signal.aborted) setReportLoading(false); });
    return () => controller.abort();
  }, [auth?.user.id, reportId]);

  async function authenticate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { acceptAuth(await api<Auth>(`/auth/${register ? 'register' : 'login'}`, { method: 'POST', body: JSON.stringify({ username, password }) })); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  async function logout() {
    if (!auth) return;
    setBusy(true); setError('');
    function clearSession() {
      if (auth) saveJob(auth.user.id, null);
      setAuth(null); setJobId(null); setJob(null); setReportId(null); setReport(null); setHistory(null); setText(''); setTitle(''); submission.current = null;
      setMode(null); setConsent(false); setHybridAvailable(false); setHybridReason(undefined);
      setInputKind('paste'); setUrl(''); setDiscovering(false);
    }
    try {
      await api<void>('/auth/logout', { method: 'POST' }, auth.csrf_token);
      clearSession();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) clearSession();
      else setError(errorMessage(err));
    }
    finally { setBusy(false); }
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); if (!auth || !inputReady || discovering) return;
    if (mode === 'hybrid' && (!hybridAvailable || !consent)) { setError('External review requires availability and your explicit consent.'); return; }
    setBusy(true); setError('');
    const body = JSON.stringify({ title: title.trim() || (inputKind === 'url' ? 'URL policy' : 'Pasted policy'),
      ...(inputKind === 'url' ? { url } : { text }), document_type: documentType, mode,
      ...(mode === 'hybrid' ? { consent: true, provider: 'groq' } : {}) });
    if (submission.current?.body !== body) submission.current = { body, key: crypto.randomUUID() };
    try {
      const created = await api<{ job_id: string; state: string }>('/analyses', { method: 'POST', body, headers: { 'Idempotency-Key': submission.current.key } }, auth.csrf_token);
      watchJob(created.job_id); submission.current = null;
      setConsent(false);
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  const activeJob = Boolean(jobId && (!job || job.state === 'queued' || job.state === 'running'));
  function watchJob(id: string) {
    if (!auth) return;
    saveJob(auth.user.id, id); setReportId(null); setReport(null); setJob(null); setJobId(id); setPollRevision(n => n + 1);
  }
  function deleted(documentId: string) {
    setReportId(null); setReport(null); setHistory(null); setHistoryPage(0); setHistoryRevision(n => n + 1); setDeletionRevision(n => n + 1);
    if (auth && job?.document_id === documentId) { saveJob(auth.user.id, null); setJobId(null); setJob(null); }
  }
  const historyContent = <>
    {!history ? <p role="status">Loading reports…</p> : history.items.length ? <ul>{history.items.map(item => <li key={item.id}><button className="history-link" onClick={() => { setReportId(item.id); setError(''); }}><strong>{item.title}</strong><span>{new Date(item.created_at).toLocaleDateString()} · {Math.round(item.coverage * 100)}% coverage · {item.effective_mode === 'hybrid' ? 'Hybrid' : 'Rules-only'}{item.degradation_reason ? ' (external review unavailable)' : ''}</span></button></li>)}</ul> : <p className="muted">Your completed reports will appear here.</p>}
    {history && history.total > 10 && <nav className="pagination" aria-label="History pages"><button disabled={!historyPage} onClick={() => setHistoryPage(p => p - 1)}>Previous</button><span>{historyPage + 1}</span><button disabled={(historyPage + 1) * 10 >= history.total} onClick={() => setHistoryPage(p => p + 1)}>Next</button></nav>}
  </>;
  return <>
    <a href="#main" className="skip-link">Skip to content</a>
    <header className="site-header"><a className="brand" href="#main">Terms<span>Lens</span></a><span className="header-note">Policy evidence, in plain view.</span>
      {auth && <div className="account"><span>{auth.user.username}</span><button disabled={busy} onClick={() => void logout()}>Log out</button></div>}
    </header>
    <main id="main" tabIndex={-1}>
      {!reportId && <div className="intro"><p className="eyebrow">Read the terms with evidence</p><h1>Understand what a policy says.</h1><p>See what needs attention, what works in your favor, and what is still unclear.</p></div>}
      {error && <div className="error" role="alert">{error}</div>}
      {starting ? <p role="status">Checking your session…</p> : !auth ? <section className="card auth-card" aria-labelledby="auth-title">
        <h2 id="auth-title">{register ? 'Create your private account' : 'Welcome back'}</h2><p>Your reports are accessible only from your account.</p>
        <form onSubmit={event => void authenticate(event)}>
          <label>Username<input name="username" autoComplete="username" required minLength={3} maxLength={32} pattern="[A-Za-z0-9_]+" value={username} onChange={e => setUsername(e.target.value)} aria-describedby="username-hint" /></label>
          <small id="username-hint">3–32 letters, numbers or underscores.</small>
          <label>Password<input name="password" type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={12} maxLength={128} value={password} onChange={e => setPassword(e.target.value)} aria-describedby="password-hint" /></label>
          <small id="password-hint">12–128 characters.</small>
          <button className="primary" disabled={busy}>{busy ? 'Please wait…' : register ? 'Create account' : 'Log in'}</button>
        </form>
        <button className="text-button" disabled={busy} onClick={() => { setRegister(v => !v); setPassword(''); setError(''); }}>{register ? 'Already registered? Log in' : 'Create an account'}</button>
      </section> : <>
        {reportId && <div className="report-toolbar"><button type="button" onClick={() => { setReportId(null); setReport(null); setConsent(false); if (!activeJob) { setJobId(null); setJob(null); } }}>← New analysis</button>
          <details className="history-picker history"><summary>Your history</summary>{historyContent}</details></div>}
        {!reportId && <div className="workspace">
          <section className="card" aria-labelledby="paste-title"><div className="section-heading"><h2 id="paste-title">Analyze a policy</h2><span className="badge">{mode === 'hybrid' ? 'Hybrid' : 'Rules-only'}</span></div>
            <p>Paste one English policy or enter its public URL. Captures and reports are saved privately.</p>
            <form onSubmit={event => void submit(event)}>
              <div className="form-row"><label>Report title<input maxLength={160} value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Store privacy policy" /></label>
                <label>Policy type<select aria-label="Policy type" value={documentType} onChange={e => { setDocumentType(e.target.value as DocumentType); setConsent(false); }}><option value="privacy">Privacy</option><option value="terms">Terms</option><option value="cookies">Cookies</option><option value="refund">Refund</option></select></label></div>
              <ReviewOptions mode={mode} consent={consent} available={hybridAvailable} unavailableReason={hybridReason} onMode={value => { setMode(value); setConsent(false); }} onConsent={setConsent} />
              <label>Policy source<select aria-label="Policy source" value={inputKind} onChange={e => { setInputKind(e.target.value as 'paste' | 'url'); setConsent(false); }}>
                <option value="paste">Paste text</option><option value="url">Public URL</option>
              </select></label>
              {inputKind === 'url' ? <URLInput key={auth.user.id} value={url} csrf={auth.csrf_token} onPending={setDiscovering}
                onChange={value => { setUrl(value); setConsent(false); }}
                onSelect={candidate => { setUrl(candidate.url); setDocumentType(candidate.document_type); setConsent(false); }} /> : <>
              <label>Policy text<textarea aria-label="Policy text" rows={10} maxLength={200000} required value={text} onChange={e => { setText(e.target.value); setConsent(false); }} aria-describedby="text-hint" /></label>
              <div className="form-meta"><small id="text-hint">{words.toLocaleString()} / 25,000 words · up to 200,000 characters</small><button type="button" className="text-button" onClick={() => { setText(example); setTitle('Engineering example'); setDocumentType('privacy'); setConsent(false); }}>Fill an example</button></div>
              </>}
              <button className="primary" disabled={busy || activeJob || discovering || !inputReady || (mode === 'hybrid' && (!hybridAvailable || !consent))}>{busy ? 'Submitting…' : activeJob ? 'Analysis in progress' : 'Analyze policy'}</button>
            </form>
          </section>
          <aside className="card history" aria-labelledby="history-title"><h2 id="history-title">Your history</h2>
            {historyContent}
          </aside>
        </div>}
        {jobId && !reportId && <section className="card progress" aria-labelledby="progress-title"><h2 id="progress-title">Analysis progress</h2><p role="status" aria-live="polite">{job ? labels[job.stage] || job.stage : 'Loading job status…'}{job?.error ? `: ${job.error}` : ''}</p>
          <ol>{stages.map((value, index) => <li key={value} className={job && index <= stages.indexOf(job.stage) ? 'reached' : ''} aria-current={job?.stage === value ? 'step' : undefined}>{labels[value]}</li>)}</ol>
          {job?.state === 'queued' && <p className="muted">Waiting for the worker. You can leave this page and reopen the report from history after it completes.</p>}
          {job?.operation === 'refresh' && job.checked_at && <p>Source checked {new Date(job.checked_at).toLocaleString()}. {job.capture_changed ? 'A new version was captured.' : 'The captured content is unchanged.'} {job.result_reused ? 'Reopened the compatible saved report without another external review.' : ''}</p>}
          <div className="pagination"><button onClick={() => { setError(''); setPollRevision(n => n + 1); }}>Check status again</button><button onClick={() => { saveJob(auth.user.id, null); setJobId(null); setJob(null); }}>Close progress</button></div>
        </section>}
        {reportLoading && <p role="status">Loading saved report…</p>}
        {report && <ReportView key={`report-${report.id}`} report={report} />}
        {report && <div className="report-actions" key={`actions-${report.id}`}>
          <details><summary>Versions and reanalysis</summary><DocumentHistory report={report} csrf={auth.csrf_token} hybridAvailable={hybridAvailable} hybridReason={hybridReason} activeJob={activeJob} onJob={watchJob} onOpen={setReportId} /></details>
          <details><summary>Compare saved reports</summary><ComparisonPanel report={report} csrf={auth.csrf_token} /></details>
          <details><summary>Manage this document</summary><DocumentDelete documentId={report.document_id} csrf={auth.csrf_token} onAccepted={deleted} /></details>
        </div>}
        <DeletionStatus key={auth.user.id} revision={deletionRevision} />
      </>}
      <footer>Automated analysis, not legal advice. Rules cover selected patterns across eight clause groups; unfamiliar wording remains unrated.</footer>
    </main>
  </>;
}
