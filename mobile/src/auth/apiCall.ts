import { toApiError } from '../api/errors';
import type { CallResult } from './tokenManager';

/** `Authorization` header for an authenticated call. */
export function bearer(token: string | null): { Authorization?: string } {
  return token === null ? {} : { Authorization: `Bearer ${token}` };
}

/** Runs one generated-client call and maps it to a CallResult (no HTTP response = offline). */
export async function runCall<T>(
  call: () => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<CallResult<T>> {
  try {
    const { data, error, response } = await call();
    if (response.ok) return { ok: true, status: response.status, data: (data ?? null) as T };
    return { ok: false, status: response.status, error: toApiError(response.status, error) };
  } catch {
    return { ok: false, status: 0, offline: true };
  }
}
