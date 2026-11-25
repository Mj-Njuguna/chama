import { type QueryKey, useQuery } from '@tanstack/react-query';

import type { AuthedCall, AuthedResult } from '../../auth/tokenManager';

/** Why a read failed. `signed_out` is handled by the session provider (login screen). */
export type ReadFailure = 'offline' | 'signed_out' | 'failed';

export class ReadError extends Error {
  constructor(readonly failure: ReadFailure) {
    super(failure);
    this.name = 'ReadError';
  }
}

export type CallAuthed = <T>(call: AuthedCall<T>) => Promise<AuthedResult<T>>;

/**
 * A server read through TanStack Query. On failure the last good data stays cached and is shown
 * read-only with an offline or error notice; nothing is retried silently.
 */
export function useAuthedQuery<T>(
  key: QueryKey,
  call: AuthedCall<T>,
  callAuthed: CallAuthed,
  enabled = true,
) {
  return useQuery<T, ReadError>({
    queryKey: key,
    enabled,
    queryFn: async () => {
      const result = await callAuthed(call);
      if (result.ok) return result.data;
      if ('offline' in result) throw new ReadError('offline');
      if ('signedOut' in result) throw new ReadError('signed_out');
      throw new ReadError('failed');
    },
  });
}
