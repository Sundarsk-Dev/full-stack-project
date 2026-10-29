import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from './api';
import type { DeletionReceipt } from './types';

export default function DocumentDelete({ documentId, csrf, onAccepted }: { documentId: string; csrf: string; onAccepted: (id: string) => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const cancel = useRef<HTMLButtonElement>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => { if (confirming) cancel.current?.focus(); }, [confirming]);
  async function remove() {
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError('');
    try {
      await api<DeletionReceipt>(`/documents/${documentId}`, { method: 'DELETE', signal: controller.signal }, csrf);
      if (!controller.signal.aborted) onAccepted(documentId);
    } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="card deletion-panel" aria-labelledby="delete-title"><h2 id="delete-title">Delete this document</h2>
    <p>Removes every captured version, report revision and comparison involving this document. Other documents stay available. Active jobs must finish first.</p>
    {error && <p className="error" role="alert">{error}</p>}
    {confirming ? <div role="group" aria-label="Confirm document deletion"><p>This cannot be undone. The document is hidden immediately; saved files are then removed in the background.</p>
      <button type="button" disabled={busy} onClick={() => void remove()}>{busy ? 'Requesting deletion…' : 'Confirm deletion'}</button>{' '}
      <button type="button" ref={cancel} disabled={busy} onClick={() => setConfirming(false)}>Cancel deletion</button>
    </div> : <button type="button" onClick={() => setConfirming(true)}>Delete all versions of this document</button>}
  </section>;
}

export function DeletionStatus({ revision }: { revision: number }) {
  const [data, setData] = useState<{ items: DeletionReceipt[]; total: number } | null>(null);
  const [page, setPage] = useState(0);
  const [error, setError] = useState('');
  useEffect(() => { setPage(0); }, [revision]);
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<{ items: DeletionReceipt[]; total: number }>(`/deletions?limit=10&offset=${page * 10}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setData(result); setError('');
        if (result.items.some(item => item.state === 'pending')) timer = setTimeout(poll, 2000);
      } catch (err) { if (!controller.signal.aborted) setError(errorMessage(err)); }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [revision, page]);
  if (!error && !data?.items.length) return null;
  return <section className="card deletion-panel" aria-labelledby="deletion-status-title"><h2 id="deletion-status-title">Deletion status</h2>
    {error && <p className="error" role="alert">{error}</p>}
    <ul aria-live="polite">{data?.items.map(item => <li key={item.document_id}>Requested {new Date(item.created_at).toLocaleString()}: {item.state === 'complete' ? 'deleted' : item.error_code ? 'waiting for cleanup; it will retry automatically' : 'queued for cleanup'}.</li>)}</ul>
    {data && data.total > 10 && <nav className="pagination" aria-label="Deletion status pages"><button disabled={!page} onClick={() => setPage(n => n - 1)}>Previous deletions</button><span>{page + 1}</span><button disabled={(page + 1) * 10 >= data.total} onClick={() => setPage(n => n + 1)}>Next deletions</button></nav>}
  </section>;
}
