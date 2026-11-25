/**
 * SecureVault: the ONLY place the refresh token is persisted. Never AsyncStorage, the query
 * cache, navigation params or logs.
 *
 * Implementations:
 * - `LocalAuthVault` (src/auth/localAuthVault.ts): expo-secure-store + one system prompt
 *   (biometrics or screen lock) per unlock.
 * - `FakeVault` (src/auth/dev/fakeVault.ts): in-memory, tests only.
 *
 * Contract every implementation keeps:
 * - `unlock` shows at most ONE system prompt (biometrics or screen lock). After `ok`, `load` and
 *   `store` never prompt until `lock` or `wipe` (token rotation never prompts).
 * - The first `store` after a login may show one "confirm it's you" prompt.
 * - `lock` forgets the unlocked state (idle re-lock, background); stored data is kept.
 * - `wipe` deletes the stored token; the next login starts from scratch.
 * - Data stored for one binding (user + installation) never loads for another: `unreadable`.
 */

/** What the stored token is bound to. */
export type VaultBinding = {
  readonly userId: string;
  readonly installationId: string;
};

export type UnlockResult =
  | { readonly kind: 'ok' }
  /** Nothing stored: show login. */
  | { readonly kind: 'empty' }
  /** Prompt cancelled or failed: stay locked, keep the stored token. */
  | { readonly kind: 'cancelled' }
  /** The device has no screen lock: show the "set a screen lock" screen. */
  | { readonly kind: 'no_screen_lock' }
  /** Stored data can no longer be decrypted (screen lock removed, key invalidated, wrong binding). */
  | { readonly kind: 'unreadable' };

export type LoadResult =
  | { readonly kind: 'ok'; readonly refreshToken: string }
  | { readonly kind: 'empty' }
  /** `unlock` has not succeeded since the last `lock`. */
  | { readonly kind: 'locked' }
  | { readonly kind: 'unreadable' };

export type StoreResult =
  | { readonly kind: 'ok' }
  /** The first-store prompt was cancelled: nothing stored. */
  | { readonly kind: 'cancelled' }
  | { readonly kind: 'no_screen_lock' }
  /** Rotation while locked or with a different binding: refused, nothing changed. */
  | { readonly kind: 'locked' };

export interface SecureVault {
  unlock(binding: VaultBinding): Promise<UnlockResult>;
  load(): Promise<LoadResult>;
  store(refreshToken: string, binding: VaultBinding): Promise<StoreResult>;
  lock(): Promise<void>;
  wipe(): Promise<void>;
}
