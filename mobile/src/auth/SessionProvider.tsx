import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { AppState, type AppStateStatus } from 'react-native';

import type { LoginResponse } from '../features/login/loginApi';
import { IdleTracker } from './idle';
import type { MonotonicClock } from './monotonicClock';
import type { LoginResult, Session } from './session';
import type { SessionApi } from './sessionApi';
import type { SignedOutReason } from './signedOut';
import type { SignedOutChannel } from './signedOutChannel';

/** What screens may know about the session. Never tokens, never the phone number. */
export type AuthState =
  | { readonly status: 'starting' }
  | { readonly status: 'signed_out'; readonly reason: SignedOutReason | null }
  /** Stored session, not unlocked. `cancelled`: the last prompt was cancelled or failed. */
  | { readonly status: 'locked'; readonly cancelled: boolean }
  | { readonly status: 'no_screen_lock' }
  | {
      readonly status: 'signed_in';
      readonly membershipCount: number | null;
      /**
       * Server PIN, from GET /me. `missing`: the user must create one before using the
       * app. `unknown`: /me could not be read (offline); nothing is blocked.
       */
      readonly pin: PinState;
    };

export type PinState = 'checking' | 'set' | 'missing' | 'unknown';

export type SessionActions = {
  completeLogin(login: LoginResponse): Promise<LoginResult>;
  unlock(): Promise<void>;
  lock(): Promise<void>;
  logout(scope: 'this_device' | 'everywhere'): Promise<void>;
  /** The first server PIN was set (PUT /auth/pin answered 204 or pin_already_set). */
  pinSet(): void;
};

type SessionContextValue = SessionActions & { readonly state: AuthState; readonly session: Session };

const SessionContext = createContext<SessionContextValue | null>(null);

/**
 * The auth state machine:
 *
 *   starting -> locked (stored session; always unlock on cold start) | signed_out
 *   signed_out -> signed_in (login)
 *   locked -> signed_in (unlock ok) | locked (cancelled) | no_screen_lock | signed_out (unreadable)
 *   signed_in -> locked (5 min in background) | signed_out (logout, terminal error)
 *
 * Signed-in content is only rendered in `signed_in`; a cancelled unlock never shows content.
 */
export function SessionProvider({
  session,
  sessionApi,
  signedOut,
  idle,
  clock,
  children,
}: {
  session: Session;
  sessionApi: SessionApi;
  signedOut: SignedOutChannel;
  idle?: IdleTracker;
  /** Monotonic clock for idle re-lock (never the wall clock). Defaults to performance.now(). */
  clock?: MonotonicClock;
  children: ReactNode;
}) {
  const [state, setState] = useState<AuthState>({ status: 'starting' });
  const [tracker] = useState(() => idle ?? new IdleTracker());
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => signedOut.subscribe((reason) => setState({ status: 'signed_out', reason })), [signedOut]);

  useEffect(() => {
    let live = true;
    void session.hasStoredSession().then((stored) => {
      if (!live) return;
      setState(stored ? { status: 'locked', cancelled: false } : { status: 'signed_out', reason: null });
    });
    return () => {
      live = false;
    };
  }, [session]);

  const loadSummary = useCallback(async () => {
    const result = await session.tokens.callAuthed(sessionApi.me);
    // Only if still signed in (a lock or sign-out may have happened meanwhile).
    if (!result.ok) {
      setState((prev) =>
        prev.status === 'signed_in' && prev.pin === 'checking' ? { ...prev, pin: 'unknown' } : prev,
      );
      return;
    }
    const membershipCount = result.data.memberships.length;
    const pin: PinState = result.data.has_pin ? 'set' : 'missing';
    setState((prev) => (prev.status === 'signed_in' ? { status: 'signed_in', membershipCount, pin } : prev));
  }, [session, sessionApi]);

  const lock = useCallback(async () => {
    if (stateRef.current.status !== 'signed_in') return;
    setState({ status: 'locked', cancelled: false }); // hide content first
    await session.lock();
  }, [session]);

  useEffect(() => {
    const onChange = (next: AppStateStatus) => {
      const now = (clock ?? defaultClock)();
      if (next === 'active') {
        if (tracker.onForeground(now)) void lock();
      } else {
        tracker.onBackground(now);
      }
    };
    const subscription = AppState.addEventListener('change', onChange);
    return () => subscription.remove();
  }, [tracker, lock, clock]);

  const value = useMemo<SessionContextValue>(
    () => ({
      state,
      session,
      lock,
      async completeLogin(login) {
        const result = await session.completeLogin(login, login.user.id);
        if (result.kind === 'ok') {
          setState({ status: 'signed_in', membershipCount: login.memberships.length, pin: 'checking' });
          // The sign-in response has no has_pin: read it from /me.
          void loadSummary();
        }
        return result;
      },
      async unlock() {
        const result = await session.unlock();
        switch (result.kind) {
          case 'ok':
            setState({ status: 'signed_in', membershipCount: null, pin: 'checking' });
            void loadSummary();
            return;
          case 'cancelled':
            setState({ status: 'locked', cancelled: true });
            return;
          case 'no_screen_lock':
            setState({ status: 'no_screen_lock' });
            return;
          case 'signed_out':
            // Unreadable data was reported through `signedOut`; an empty vault needs no message.
            setState((prev) =>
              prev.status === 'signed_out' ? prev : { status: 'signed_out', reason: null },
            );
        }
      },
      pinSet() {
        setState((prev) => (prev.status === 'signed_in' ? { ...prev, pin: 'set' } : prev));
      },
      async logout(scope) {
        // Best effort: the local wipe happens even when offline or the server refuses.
        await session.tokens.callAuthed(scope === 'everywhere' ? sessionApi.logoutAll : sessionApi.logout);
        await session.logout();
      },
    }),
    [state, session, sessionApi, lock, loadSummary],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

const defaultClock: MonotonicClock = () => performance.now();

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) throw new Error('useSession outside SessionProvider');
  return value;
}
