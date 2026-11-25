import type { ApiError } from '../../api/errors';
import {
  type AuthedCall,
  GRACE_RETRY_BUDGET_MS,
  type RefreshCallResult,
  type TerminalReason,
  TokenManager,
} from '../tokenManager';

const pair = (n: number) => ({
  session_id: 'abbe778d-812f-4251-bc99-1934ee33aebb',
  access_token: `chm_at_access${n}`,
  access_expires_in: 900,
  refresh_token: `chm_rt_refresh${n}`,
  refresh_expires_in: 2592000,
});

const apiError = (
  status: number,
  code: ApiError['code'],
  extra: Partial<ApiError> = {},
): RefreshCallResult => ({
  kind: 'api',
  error: { kind: 'api', status, code, ...extra },
});

type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

function setup(callRefresh: (token: string) => Promise<RefreshCallResult>) {
  let clock = 0;
  const persisted: string[] = [];
  const terminal: TerminalReason[] = [];
  const order: string[] = [];
  const manager = new TokenManager({
    callRefresh: jest.fn(callRefresh),
    persistRefreshToken: jest.fn(async (token: string) => {
      order.push(`persist:${token}`);
      persisted.push(token);
    }),
    onTerminal: jest.fn(async (reason: TerminalReason) => {
      terminal.push(reason);
    }),
    now: () => clock,
    sleep: jest.fn(async (ms: number) => {
      clock += ms;
    }),
  });
  manager.startSession({ refreshToken: 'chm_rt_refresh0', accessToken: 'chm_at_access0' });
  return { manager, persisted, terminal, order, advance: (ms: number) => (clock += ms) };
}

/** A fake authenticated endpoint: 401 token_expired unless called with an accepted token. */
function endpoint(accepted: Set<string>) {
  const seen: (string | null)[] = [];
  const call: AuthedCall<string> = async (accessToken) => {
    seen.push(accessToken);
    if (accessToken !== null && accepted.has(accessToken)) return { ok: true, status: 200, data: 'payload' };
    return { ok: false, status: 401, error: { kind: 'api', status: 401, code: 'token_expired' } };
  };
  return { call, seen };
}

describe('refresh', () => {
  it('stores the new refresh token BEFORE the new access token is used', async () => {
    const { manager, order } = setup(async (token) => {
      order.push(`refresh:${token}`);
      return { kind: 'ok', pair: pair(1) };
    });
    await expect(manager.refresh()).resolves.toEqual({ kind: 'ok' });
    expect(order).toEqual(['refresh:chm_rt_refresh0', 'persist:chm_rt_refresh1']);
    expect(manager.getAccessToken()).toBe('chm_at_access1');
  });

  it('concurrent callers share ONE refresh in flight', async () => {
    const gate = deferred<RefreshCallResult>();
    const { manager } = setup(() => gate.promise);
    const results = Promise.all([manager.refresh(), manager.refresh(), manager.refresh()]);
    gate.resolve({ kind: 'ok', pair: pair(1) });
    await expect(results).resolves.toEqual([{ kind: 'ok' }, { kind: 'ok' }, { kind: 'ok' }]);
    expect(manager.deps.callRefresh).toHaveBeenCalledTimes(1);
  });

  it('rotates: the next refresh uses the NEW refresh token', async () => {
    let n = 0;
    const { manager } = setup(async () => ({ kind: 'ok', pair: pair(++n) }));
    await manager.refresh();
    await manager.refresh();
    expect(manager.deps.callRefresh).toHaveBeenNthCalledWith(2, 'chm_rt_refresh1');
  });

  it.each(['refresh_reused', 'session_revoked', 'session_expired', 'invalid_token'] as const)(
    '%s wipes credentials, signs out, and never retries',
    async (code) => {
      const { manager, terminal } = setup(async () => apiError(401, code));
      await expect(manager.refresh()).resolves.toEqual({ kind: 'signed_out', reason: code });
      expect(manager.deps.callRefresh).toHaveBeenCalledTimes(1);
      expect(terminal).toEqual([code]);
      expect(manager.getAccessToken()).toBeNull();
      expect(manager.hasSession()).toBe(false);
    },
  );

  it('a malformed refresh request (validation_error) also ends the session', async () => {
    const { manager, terminal } = setup(async () => apiError(400, 'validation_error'));
    await expect(manager.refresh()).resolves.toEqual({ kind: 'signed_out', reason: 'invalid_token' });
    expect(terminal).toEqual(['invalid_token']);
  });

  it('rate_limited keeps the session and reports the wait, without retrying', async () => {
    const { manager, terminal } = setup(async () => apiError(429, 'rate_limited', { retryAfter: 30 }));
    await expect(manager.refresh()).resolves.toEqual({ kind: 'rate_limited', retryAfter: 30 });
    expect(manager.deps.callRefresh).toHaveBeenCalledTimes(1);
    expect(terminal).toEqual([]);
    expect(manager.hasSession()).toBe(true);
  });

  it('without a session there is nothing to refresh', async () => {
    const { manager } = setup(async () => ({ kind: 'ok', pair: pair(1) }));
    manager.clear();
    await expect(manager.refresh()).resolves.toEqual({ kind: 'signed_out', reason: 'no_session' });
    expect(manager.deps.callRefresh).not.toHaveBeenCalled();
  });

  it('a refresh answer that arrives after the session was cleared is discarded', async () => {
    const gate = deferred<RefreshCallResult>();
    const { manager, persisted } = setup(() => gate.promise);
    const pending = manager.refresh();
    manager.clear(); // e.g. the user pressed log out meanwhile
    gate.resolve({ kind: 'ok', pair: pair(1) });
    await expect(pending).resolves.toEqual({ kind: 'signed_out', reason: 'no_session' });
    expect(persisted).toEqual([]);
    expect(manager.getAccessToken()).toBeNull();
  });
});

describe('closeForLock (lock during an in-flight rotation)', () => {
  it('waits for the refresh in flight, so the rotated token is stored before the tokens go', async () => {
    const gate = deferred<RefreshCallResult>();
    const { manager, persisted } = setup(() => gate.promise);
    const refreshing = manager.refresh();
    let closed = false;
    const closing = manager.closeForLock().then(() => (closed = true));
    await Promise.resolve();
    expect(closed).toBe(false);
    gate.resolve({ kind: 'ok', pair: pair(1) });
    await expect(refreshing).resolves.toEqual({ kind: 'ok' });
    await closing;
    expect(persisted).toEqual(['chm_rt_refresh1']);
    expect(manager.hasSession()).toBe(false);
  });

  it('RACE: a caller that starts a new refresh right after the in-flight one never reaches the server', async () => {
    const gate = deferred<RefreshCallResult>();
    const { manager, persisted } = setup(() => gate.promise);
    const first = manager.refresh();
    // A caller waiting on the same refresh resumes BEFORE the lock does, and refreshes again
    // (for example a request that still got 401). Without this guard a second refresh rotated the
    // token on the server and was then thrown away: the next unlock got refresh_reused.
    let second: Promise<unknown> | null = null;
    void first.then(() => {
      second = manager.refresh();
    });
    const closing = manager.closeForLock();
    gate.resolve({ kind: 'ok', pair: pair(1) });
    await closing;
    expect(second).not.toBeNull();
    await expect(second).resolves.toEqual({ kind: 'signed_out', reason: 'no_session' });
    expect(manager.deps.callRefresh).toHaveBeenCalledTimes(1);
    expect(persisted).toEqual(['chm_rt_refresh1']);
  });

  it('while closing, a refresh only joins the one in flight (one server call)', async () => {
    const gate = deferred<RefreshCallResult>();
    const { manager } = setup(() => gate.promise);
    const first = manager.refresh();
    const closing = manager.closeForLock();
    const joined = manager.refresh();
    expect(joined).toBe(first);
    gate.resolve({ kind: 'ok', pair: pair(1) });
    await closing;
    expect(manager.deps.callRefresh).toHaveBeenCalledTimes(1);
  });

  it('with nothing in flight it clears at once, and refuses new refreshes until the next session', async () => {
    const { manager } = setup(async () => ({ kind: 'ok', pair: pair(1) }));
    await manager.closeForLock();
    await expect(manager.refresh()).resolves.toEqual({ kind: 'signed_out', reason: 'no_session' });
    expect(manager.deps.callRefresh).not.toHaveBeenCalled();
    manager.startSession({ refreshToken: 'chm_rt_refresh0', accessToken: null });
    await expect(manager.refresh()).resolves.toEqual({ kind: 'ok' });
  });
});

describe('refresh grace window (lost responses)', () => {
  it('retries a lost refresh with the SAME token and accepts the identical pair', async () => {
    const answers: RefreshCallResult[] = [
      { kind: 'network' },
      { kind: 'network' },
      { kind: 'ok', pair: pair(1) },
    ];
    const { manager } = setup(async () => answers.shift() ?? { kind: 'network' });
    await expect(manager.refresh()).resolves.toEqual({ kind: 'ok' });
    expect(manager.deps.callRefresh).toHaveBeenCalledTimes(3);
    for (const call of (manager.deps.callRefresh as jest.Mock).mock.calls) {
      expect(call).toEqual(['chm_rt_refresh0']);
    }
  });

  it('stops retrying inside the server grace window and keeps the session (offline)', async () => {
    const { manager, terminal } = setup(async () => ({ kind: 'network' }));
    await expect(manager.refresh()).resolves.toEqual({ kind: 'network' });
    // Every retry was sent before the budget, which is below the server's 10 s grace window.
    expect(GRACE_RETRY_BUDGET_MS).toBeLessThan(10_000);
    expect(terminal).toEqual([]);
    expect(manager.hasSession()).toBe(true);
  });

  it('SHARP EDGE: a refresh retried after the grace window gets refresh_reused and forces sign-in', async () => {
    // First attempt: the server rotated, but the answer was lost and every in-budget retry failed too.
    // Much later (> 10 s), the app tries again with the old token: the server sees a reuse.
    let attempts = 0;
    const { manager, terminal, advance } = setup(async () => {
      attempts += 1;
      return attempts <= 5 ? { kind: 'network' } : apiError(401, 'refresh_reused');
    });
    await expect(manager.refresh()).resolves.toEqual({ kind: 'network' });
    advance(60_000);
    await expect(manager.refresh()).resolves.toEqual({ kind: 'signed_out', reason: 'refresh_reused' });
    expect(terminal).toEqual(['refresh_reused']);
    expect(manager.hasSession()).toBe(false);
  });
});

describe('callAuthed', () => {
  it('attaches the current access token', async () => {
    const { manager } = setup(async () => ({ kind: 'ok', pair: pair(1) }));
    const api = endpoint(new Set(['chm_at_access0']));
    await expect(manager.callAuthed(api.call)).resolves.toEqual({ ok: true, status: 200, data: 'payload' });
    expect(api.seen).toEqual(['chm_at_access0']);
    expect(manager.deps.callRefresh).not.toHaveBeenCalled();
  });

  it('refreshes first when unlocked without an access token (cold start)', async () => {
    const { manager } = setup(async () => ({ kind: 'ok', pair: pair(1) }));
    manager.startSession({ refreshToken: 'chm_rt_refresh0', accessToken: null });
    const api = endpoint(new Set(['chm_at_access1']));
    await expect(manager.callAuthed(api.call)).resolves.toMatchObject({ ok: true });
    expect(api.seen).toEqual(['chm_at_access1']);
  });

  it.each(['token_expired', 'invalid_token'] as const)(
    'on 401 %s: refresh once, retry once with the new token',
    async (code) => {
      const { manager } = setup(async () => ({ kind: 'ok', pair: pair(1) }));
      const seen: (string | null)[] = [];
      const call: AuthedCall<string> = async (token) => {
        seen.push(token);
        return token === 'chm_at_access1'
          ? { ok: true, status: 200, data: 'payload' }
          : { ok: false, status: 401, error: { kind: 'api', status: 401, code } };
      };
      await expect(manager.callAuthed(call)).resolves.toMatchObject({ ok: true });
      expect(seen).toEqual(['chm_at_access0', 'chm_at_access1']);
    },
  );

  it('several requests expiring at once share one refresh; each retries once', async () => {
    const gate = deferred<RefreshCallResult>();
    const { manager } = setup(() => gate.promise);
    const api = endpoint(new Set(['chm_at_access1']));
    const calls = Promise.all([1, 2, 3, 4].map(() => manager.callAuthed(api.call)));
    await Promise.resolve();
    gate.resolve({ kind: 'ok', pair: pair(1) });
    const results = await calls;
    expect(results.every((r) => r.ok)).toBe(true);
    expect(manager.deps.callRefresh).toHaveBeenCalledTimes(1);
    expect(api.seen.filter((t) => t === 'chm_at_access0')).toHaveLength(4);
    expect(api.seen.filter((t) => t === 'chm_at_access1')).toHaveLength(4);
  });

  it('does not loop: a second 401 after the retry is returned as is', async () => {
    const { manager } = setup(async () => ({ kind: 'ok', pair: pair(1) }));
    const api = endpoint(new Set());
    const result = await manager.callAuthed(api.call);
    expect(result).toMatchObject({ ok: false, status: 401 });
    expect(api.seen).toHaveLength(2);
    expect(manager.deps.callRefresh).toHaveBeenCalledTimes(1);
  });

  it.each(['session_revoked', 'session_expired', 'refresh_reused'] as const)(
    '401 %s on any call signs out immediately, no refresh, no retry',
    async (code) => {
      const { manager, terminal } = setup(async () => ({ kind: 'ok', pair: pair(1) }));
      const seen: (string | null)[] = [];
      const result = await manager.callAuthed(async (token) => {
        seen.push(token);
        return { ok: false, status: 401, error: { kind: 'api', status: 401, code } };
      });
      expect(result).toEqual({ ok: false, status: 0, signedOut: code });
      expect(seen).toHaveLength(1);
      expect(manager.deps.callRefresh).not.toHaveBeenCalled();
      expect(terminal).toEqual([code]);
    },
  );

  it('when the refresh ends the session, the original call is not retried', async () => {
    const { manager, terminal } = setup(async () => apiError(401, 'refresh_reused'));
    const api = endpoint(new Set());
    await expect(manager.callAuthed(api.call)).resolves.toEqual({
      ok: false,
      status: 0,
      signedOut: 'refresh_reused',
    });
    expect(api.seen).toHaveLength(1);
    expect(terminal).toEqual(['refresh_reused']);
  });

  it('when the refresh fails for network reasons, reports offline and keeps the session', async () => {
    const { manager } = setup(async () => ({ kind: 'network' }));
    const api = endpoint(new Set());
    await expect(manager.callAuthed(api.call)).resolves.toEqual({ ok: false, status: 0, offline: true });
    expect(manager.hasSession()).toBe(true);
  });
});
