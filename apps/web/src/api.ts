export class ApiError extends Error {
  status: number;
  requestId: string | null;
  constructor(message: string, status: number, requestId: string | null = null) { super(message); this.status = status; this.requestId = requestId; }
}

export async function api<T>(path: string, options: RequestInit = {}, csrf?: string, timeoutMs = 10000): Promise<T> {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 65000) throw new Error('Invalid request timeout.');
  const controller = new AbortController();
  const abort = () => controller.abort();
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, timeoutMs);
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  if (csrf) headers.set('X-CSRF-Token', csrf);
  try {
    const response = await fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin', signal: controller.signal });
    if (response.status === 204) return undefined as T;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = data?.detail;
      const message = typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map((item: { msg?: string }) => item.msg || 'Invalid input').join(' ')
        : `Request failed (${response.status}).`;
      throw new ApiError(message, response.status, response.headers.get('X-Request-ID'));
    }
    if (data === null) throw new Error('The server returned an unreadable response.');
    return data as T;
  } finally {
    clearTimeout(timeout);
    options.signal?.removeEventListener('abort', abort);
  }
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message + (error.requestId ? ` Reference: ${error.requestId}` : '');
  if (error instanceof Error && error.name === 'AbortError') return 'The request timed out. Check the connection and try again.';
  return 'Could not reach TermsLens. Check that the API is running, then try again.';
}
