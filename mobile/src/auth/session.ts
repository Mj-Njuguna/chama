import type { Schemas } from '../api/client';
import { type DeviceIdentityStore, ensureInstallationId } from './deviceIdentity';
import type { SignedOutReason } from './signedOut';
import { type RefreshCallResult, type TerminalReason, TokenManager } from './tokenManager';
import type { SecureVault, VaultBinding } from './vault';

type TokenPair = Schemas['TokenPair'];

export type SessionDeps = {
  readonly vault: SecureVault;
  readonly identity: DeviceIdentityStore;
  readonly callRefresh: (refreshToken: string) => Promise<RefreshCallResult>;
  readonly newInstallationId: () => string;
  /** Called after every wipe that should show the login screen. */
  readonly onSignedOut: (reason: SignedOutReason) => void;
  readonly now?: () => number;
  readonly sleep?: (ms: number) => Promise<void>;
};

export type LoginResult = { readonly kind: 'ok' } | { readonly kind: 'cancelled' | 'no_screen_lock' };

export type UnlockOutcome =
  | { readonly kind: 'ok' }
  | { readonly kind: 'signed_out' }
  | { readonly kind: 'cancelled' }
  | { readonly kind: 'no_screen_lock' };

/** Raised inside the token manager when a rotated refresh token could not be stored. */
class VaultWriteError extends Error {}

/**
 * Ties the SecureVault, the device identity and the TokenManager together. Screens talk to this; they never touch tokens.
 */
export class Session {
  readonly tokens: TokenManager;
  private binding: VaultBinding | null = null;
  /** A lock in progress: an unlock that arrives meanwhile waits for it to finish. */
  private locking: Promise<void> | null = null;

  constructor(private readonly deps: SessionDeps) {
    this.tokens = new TokenManager({
      callRefresh: deps.callRefresh,
      persistRefreshToken: (token) => this.persist(token),
      onTerminal: (reason) => this.wipeTerminal(reason),
      now: deps.now ?? Date.now,
      sleep: deps.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms))),
    });
  }

  /** installation_id for `POST /auth/otp/request` and `/auth/otp/verify`. */
  installationId(): Promise<string> {
    return ensureInstallationId(this.deps.identity, this.deps.newInstallationId);
  }

  /**
   * After `POST /auth/otp/verify`. Stores the refresh token (the first store may show one
   * "confirm it's you" prompt). On cancel nothing is stored and no session starts; the caller
   * may call again with the same pair.
   */
  async completeLogin(pair: TokenPair, userId: string): Promise<LoginResult> {
    const binding: VaultBinding = { userId, installationId: await this.installationId() };
    const stored = await this.deps.vault.store(pair.refresh_token, binding);
    if (stored.kind === 'cancelled' || stored.kind === 'no_screen_lock') return { kind: stored.kind };
    if (stored.kind !== 'ok') throw new VaultWriteError('vault refused the first store');
    await this.deps.identity.setUserId(userId);
    this.binding = binding;
    this.tokens.startSession({ refreshToken: pair.refresh_token, accessToken: pair.access_token });
    return { kind: 'ok' };
  }

  /** Is there a stored session to unlock (cold start: unlock screen vs login)? */
  async hasStoredSession(): Promise<boolean> {
    const [userId, installationId] = await Promise.all([
      this.deps.identity.getUserId(),
      this.deps.identity.getInstallationId(),
    ]);
    return userId !== null && installationId !== null;
  }

  /** Cold start or after idle re-lock: one system prompt, then the refresh token is in memory. */
  async unlock(): Promise<UnlockOutcome> {
    // The unlock screen can appear (and prompt) before an idle re-lock has finished waiting for a
    // refresh. Unlocking then would load the OLD stored token and start a new session, so the
    // in-flight rotation would be thrown away and the next refresh would get refresh_reused.
    if (this.locking !== null) await this.locking;
    const [userId, installationId] = await Promise.all([
      this.deps.identity.getUserId(),
      this.deps.identity.getInstallationId(),
    ]);
    if (userId === null || installationId === null) {
      await this.deps.vault.wipe();
      return { kind: 'signed_out' };
    }
    const binding: VaultBinding = { userId, installationId };
    const unlocked = await this.deps.vault.unlock(binding);
    switch (unlocked.kind) {
      case 'cancelled':
      case 'no_screen_lock':
        return { kind: unlocked.kind };
      case 'empty':
        await this.deps.identity.clear('user');
        return { kind: 'signed_out' };
      case 'unreadable':
        await this.wipeTerminal('unreadable_credentials');
        return { kind: 'signed_out' };
      case 'ok':
        break;
    }
    const loaded = await this.deps.vault.load();
    if (loaded.kind !== 'ok') {
      await this.wipeTerminal('unreadable_credentials');
      return { kind: 'signed_out' };
    }
    this.binding = binding;
    // The access token is not stored: the first API call refreshes.
    this.tokens.startSession({ refreshToken: loaded.refreshToken, accessToken: null });
    return { kind: 'ok' };
  }

  /**
   * Idle re-lock or explicit lock. Waits for an in-flight refresh first and lets no new one
   * start: locking the vault mid-rotation would lose the new refresh token and force a
   * sign-in later.
   */
  lock(): Promise<void> {
    this.locking ??= (async () => {
      await this.tokens.closeForLock();
      this.binding = null;
      await this.deps.vault.lock();
    })().finally(() => {
      this.locking = null;
    });
    return this.locking;
  }

  /** User tapped Log out / Log out everywhere (server call is the caller's, best effort). */
  async logout(): Promise<void> {
    this.tokens.clear();
    this.binding = null;
    await this.deps.vault.wipe();
    await this.deps.identity.clear('user');
    this.deps.onSignedOut('user_logout');
  }

  private async persist(refreshToken: string): Promise<void> {
    if (this.binding === null) throw new VaultWriteError('no binding');
    const stored = await this.deps.vault.store(refreshToken, this.binding);
    if (stored.kind !== 'ok') throw new VaultWriteError(`vault refused rotation: ${stored.kind}`);
  }

  private async wipeTerminal(reason: TerminalReason | 'unreadable_credentials'): Promise<void> {
    this.tokens.clear();
    this.binding = null;
    await this.deps.vault.wipe();
    await this.deps.identity.clear('all');
    this.deps.onSignedOut(reason);
  }
}
