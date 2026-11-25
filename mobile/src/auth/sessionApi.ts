import type { ApiClient, Schemas } from '../api/client';
import { bearer, runCall } from './apiCall';
import type { AuthedCall, CallResult } from './tokenManager';

export type Me = Schemas['Me'];

/** Authenticated session calls; each takes the access token from the TokenManager. */
export type SessionApi = {
  readonly me: AuthedCall<Me>;
  readonly logout: AuthedCall<null>;
  readonly logoutAll: AuthedCall<null>;
};

export function createSessionApi(client: ApiClient): SessionApi {
  return {
    me: (token) => runCall(() => client.GET('/api/v1/me', { headers: bearer(token) })),
    logout: (token) =>
      runCall(() => client.POST('/api/v1/auth/logout', { headers: bearer(token) })) as Promise<
        CallResult<null>
      >,
    logoutAll: (token) =>
      runCall(() => client.POST('/api/v1/auth/logout-all', { headers: bearer(token) })) as Promise<
        CallResult<null>
      >,
  };
}
