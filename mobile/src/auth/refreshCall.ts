import type { ApiClient } from '../api/client';
import { toApiError } from '../api/errors';
import type { RefreshCallResult } from './tokenManager';

/**
 * POST /auth/token/refresh through the generated client. Any exception (no response, or a body
 * that is not JSON, e.g. a proxy's HTML page) counts as "no answer": the token manager then
 * retries with the same token inside the grace window.
 */
export function makeRefreshCall(client: ApiClient): (refreshToken: string) => Promise<RefreshCallResult> {
  return async (refreshToken) => {
    try {
      const { data, error, response } = await client.POST('/api/v1/auth/token/refresh', {
        body: { refresh_token: refreshToken },
      });
      if (data !== undefined) return { kind: 'ok', pair: data };
      return { kind: 'api', error: toApiError(response.status, error) };
    } catch {
      return { kind: 'network' };
    }
  };
}
