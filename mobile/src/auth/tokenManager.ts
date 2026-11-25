import type { ApiError } from '../api/errors';
import type { Schemas } from '../api/client';

/**
 * Access/refresh token handling.
 *
 * - The access token lives in memory only. The refresh token is persisted (secure storage) by
 *   `persistRefreshToken` BEFORE the new access token is used, so a crash never leaves the app
 *   holding only a retired refresh token.
 * - ONE refresh at a time: concurrent callers await the same in-flight promise.
 * - A refresh whose answer was lost (no HTTP response) is retried with the SAME token, but only
 *   within GRACE_RETRY_BUDGET_MS, safely inside the server's 10 s grace window.
 * - refresh_reused / session_revoked / session_expired / invalid_token on refresh end the
 *   session: memory is wiped, `onTerminal` wipes storage and returns to login. No retries.
 *
 * Sharp edge: if every in-budget retry is lost
 * and the app tries again after the grace window, the server sees a reuse, answers
 * refresh_reused, and the user must sign in again. The login screen explains it kindly.
 */

type TokenPair = Schemas['TokenPair'];

/** Total time a lost refresh may be retried with the same token; < the server's 10 s window. */
export const GRACE_RETRY_BUDGET_MS = 8_000;
/** Waits between retries of a lost refresh. */
export const GRACE_RETRY_DELAYS_MS: readonly number[] = [500, 1_500, 3_000];

export type TerminalReason = 'refresh_reused' | 'session_revoked' | 'session_expired' | 'invalid_token';

const TERMINAL_ON_REFRESH = new Set<string>([
  'refresh_reused',
  'session_revoked',
  'session_expired',
  'invalid_token',
]);
const TERMINAL_ON_CALL = new Set<string>(['refresh_reused', 'session_revoked', 'session_expired']);
const REFRESH_AND_RETRY = new Set<string>(['token_expired', 'invalid_token']);

/** The raw outcome of POST /auth/token/refresh, as produced by the API layer. */
export type RefreshCallResult =
  | { readonly kind: 'ok'; readonly pair: TokenPair }
  | { readonly kind: 'api'; readonly error: ApiError }
  | { readonly kind: 'network' };

export type RefreshOutcome =
  | { readonly kind: 'ok' }
  | { readonly kind: 'signed_out'; readonly reason: TerminalReason | 'no_session' }
  | { readonly kind: 'network' }
  | { readonly kind: 'rate_limited'; readonly retryAfter?: number };

/** What an authenticated operation returns. `offline` means no HTTP response at all. */
export type CallResult<T> =
  | { readonly ok: true; readonly status: number; readonly data: T }
  | { readonly ok: false; readonly status: number; readonly error: ApiError }
  | { readonly ok: false; readonly status: 0; readonly offline: true };

export type AuthedResult<T> =
  | CallResult<T>
  | { readonly ok: false; readonly status: 0; readonly signedOut: TerminalReason | 'no_session' }
  | { readonly ok: false; readonly status: 0; readonly rateLimited: true; readonly retryAfter?: number };

/** An API call that takes the current access token (null only when the caller must be anonymous). */
export type AuthedCall<T> = (accessToken: string | null) => Promise<CallResult<T>>;

export type TokenManagerDeps = {
  readonly callRefresh: (refreshToken: string) => Promise<RefreshCallResult>;
  readonly persistRefreshToken: (refreshToken: string) => Promise<void>;
  /** Wipe stored credentials and route to login. Called once per ended session. */
  readonly onTerminal: (reason: TerminalReason) => Promise<void>;
  readonly now: () => number;
  readonly sleep: (ms: number) => Promise<void>;
};

export class TokenManager {
  private refreshToken: string | null = null;
  private accessToken: string | null = null;
  private generation = 0;
  private inflight: Promise<RefreshOutcome> | null = null;
  /** A lock has begun: no NEW refresh may start until the next session. */
  private closing = false;

  constructor(readonly deps: TokenManagerDeps) {}

  /** After login (both tokens) or local unlock (refresh token only; access comes from a refresh). */
  startSession(tokens: { refreshToken: string; accessToken: string | null }): void {
    this.generation += 1;
    this.inflight = null;
    this.closing = false;
    this.refreshToken = tokens.refreshToken;
    this.accessToken = tokens.accessToken;
  }

  /** Forget tokens in memory (logout, lock). Storage is the caller's job. */
  clear(): void {
    this.generation += 1;
    this.inflight = null;
    this.refreshToken = null;
    this.accessToken = null;
  }

  /**
   * Before the vault locks: stop new refreshes, let the one in flight finish (it stores the
   * rotated refresh token while the vault is still open), then forget the tokens.
   *
   * Closing first matters: callers waiting on the in-flight refresh resume before this method
   * does, and one of them could start a NEW refresh. That refresh would rotate the token on the
   * server and then be discarded by clear(), so the next unlock would get refresh_reused and a
   * forced sign-in. While closing, refresh() only joins the refresh already in flight.
   */
  async closeForLock(): Promise<void> {
    this.closing = true;
    while (this.inflight !== null) {
      const current = this.inflight;
      await current.catch(() => undefined);
      if (this.inflight === current) break;
    }
    this.clear();
  }

  hasSession(): boolean {
    return this.refreshToken !== null;
  }

  getAccessToken(): string | null {
    return this.accessToken;
  }

  refresh(): Promise<RefreshOutcome> {
    if (this.closing && this.inflight === null) {
      return Promise.resolve({ kind: 'signed_out', reason: 'no_session' });
    }
    this.inflight ??= this.runRefresh().finally(() => {
      this.inflight = null;
    });
    return this.inflight;
  }

  async callAuthed<T>(call: AuthedCall<T>): Promise<AuthedResult<T>> {
    let token = this.accessToken;
    if (token === null) {
      const outcome = await this.refresh();
      if (outcome.kind !== 'ok') return failure(outcome);
      token = this.accessToken;
    }

    const first = await call(token);
    if (first.ok || !('error' in first) || first.status !== 401) return first;

    const code = first.error.code;
    if (TERMINAL_ON_CALL.has(code)) {
      await this.endSession(code as TerminalReason);
      return { ok: false, status: 0, signedOut: code as TerminalReason };
    }
    if (!REFRESH_AND_RETRY.has(code)) return first;

    // Another caller may already have rotated while this request was in flight.
    if (this.accessToken === token || this.accessToken === null) {
      const outcome = await this.refresh();
      if (outcome.kind !== 'ok') return failure(outcome);
    }
    return call(this.accessToken);
  }

  private async runRefresh(): Promise<RefreshOutcome> {
    const generation = this.generation;
    const token = this.refreshToken;
    if (token === null) return { kind: 'signed_out', reason: 'no_session' };

    const started = this.deps.now();
    let result = await this.deps.callRefresh(token);
    for (const delay of GRACE_RETRY_DELAYS_MS) {
      if (result.kind !== 'network' || generation !== this.generation) break;
      if (this.deps.now() + delay - started >= GRACE_RETRY_BUDGET_MS) break;
      await this.deps.sleep(delay);
      if (generation !== this.generation) break;
      result = await this.deps.callRefresh(token);
    }

    if (generation !== this.generation) return { kind: 'signed_out', reason: 'no_session' };

    switch (result.kind) {
      case 'ok':
        try {
          await this.deps.persistRefreshToken(result.pair.refresh_token);
        } catch {
          // The rotated token could not be stored, so the stored one is now retired: continuing
          // would only end in refresh_reused later. End the session cleanly now.
          if (generation !== this.generation) return { kind: 'signed_out', reason: 'no_session' };
          await this.endSession('invalid_token');
          return { kind: 'signed_out', reason: 'invalid_token' };
        }
        if (generation !== this.generation) return { kind: 'signed_out', reason: 'no_session' };
        this.refreshToken = result.pair.refresh_token;
        this.accessToken = result.pair.access_token;
        return { kind: 'ok' };
      case 'network':
        return { kind: 'network' };
      case 'api': {
        const { code, status, retryAfter } = result.error;
        if (code === 'rate_limited') {
          return retryAfter === undefined ? { kind: 'rate_limited' } : { kind: 'rate_limited', retryAfter };
        }
        // Anything else from the refresh endpoint (including a 400 for a malformed token) means
        // this refresh token is unusable: end the session rather than loop.
        const reason: TerminalReason = TERMINAL_ON_REFRESH.has(code)
          ? (code as TerminalReason)
          : 'invalid_token';
        if (status >= 500) return { kind: 'network' };
        await this.endSession(reason);
        return { kind: 'signed_out', reason };
      }
    }
  }

  private async endSession(reason: TerminalReason): Promise<void> {
    if (this.refreshToken === null) return; // already ended by a concurrent caller
    this.clear();
    await this.deps.onTerminal(reason);
  }
}

function failure(outcome: Exclude<RefreshOutcome, { kind: 'ok' }>): AuthedResult<never> {
  switch (outcome.kind) {
    case 'signed_out':
      return { ok: false, status: 0, signedOut: outcome.reason };
    case 'network':
      return { ok: false, status: 0, offline: true };
    case 'rate_limited':
      return outcome.retryAfter === undefined
        ? { ok: false, status: 0, rateLimited: true }
        : { ok: false, status: 0, rateLimited: true, retryAfter: outcome.retryAfter };
  }
}
