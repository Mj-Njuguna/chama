/**
 * DEV ONLY. Startup reachability check against the backend's liveness endpoint (`GET /healthz`).
 * Only the HTTP status is used, so no response type is needed. Imported only behind `__DEV__`.
 */

export const HEALTH_TIMEOUT_MS = 3000;

export type HealthFailure = 'timeout' | 'unreachable' | 'host_not_allowed' | 'http_error';

export type HealthResult =
  { readonly ok: true } | { readonly ok: false; readonly kind: HealthFailure; readonly status?: number };

type FetchLike = (input: string, init: { method: 'GET'; signal: AbortSignal }) => Promise<{ status: number }>;

export async function checkBackendHealth(
  apiBaseUrl: string,
  fetchImpl: FetchLike = fetch,
  timeoutMs: number = HEALTH_TIMEOUT_MS,
): Promise<HealthResult> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    const response = await fetchImpl(`${apiBaseUrl}/healthz`, { method: 'GET', signal: controller.signal });
    if (response.status === 200) {
      return { ok: true };
    }
    // Django answers 400 when the Host header (10.0.2.2) is not in ALLOWED_HOSTS.
    if (response.status === 400) {
      return { ok: false, kind: 'host_not_allowed', status: 400 };
    }
    return { ok: false, kind: 'http_error', status: response.status };
  } catch {
    return { ok: false, kind: timedOut ? 'timeout' : 'unreachable' };
  } finally {
    clearTimeout(timer);
  }
}
