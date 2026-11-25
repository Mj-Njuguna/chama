import type { KeyValueStore } from './keyValue';
import type { LoadResult, SecureVault, StoreResult, UnlockResult, VaultBinding } from './vault';

/** The system prompt (biometrics or the device screen lock), injectable for tests. */
export interface UserPresence {
  /** False when the device has no screen lock at all (no PIN, pattern, password or biometrics). */
  hasScreenLock(): Promise<boolean>;
  /** Shows ONE system prompt. True only when the user passed it. */
  prompt(text: { title: string; cancel: string }): Promise<boolean>;
}

const TOKEN_KEY = 'chm.vault.refresh';

type Stored = { readonly token: string; readonly userId: string; readonly installationId: string };

/**
 * LOCAL UNLOCK vault. The refresh token is kept in expo-secure-store (encrypted at rest with a
 * Keystore key on Android, the Keychain on iOS, this device only). Reading it into memory needs
 * the user to pass the system prompt (biometrics or screen lock) once per unlock.
 *
 * This is the local-unlock concept only. It has nothing to do with the server step-up PIN, which
 * the backend verifies; the app never checks a PIN.
 *
 * Contract (same as every SecureVault):
 * - `unlock` shows at most one prompt; after `ok`, `load` and `store` never prompt until `lock`.
 * - The first `store` after a login shows one prompt (proves a screen lock exists).
 * - Data stored for one binding never loads for another: `unreadable`.
 */
export class LocalAuthVault implements SecureVault {
  private unlocked = false;

  constructor(
    private readonly storage: KeyValueStore,
    private readonly presence: UserPresence,
    private readonly promptText: () => { title: string; cancel: string },
  ) {}

  async unlock(binding: VaultBinding): Promise<UnlockResult> {
    const stored = await this.read();
    if (stored === 'empty') return { kind: 'empty' };
    if (stored === 'unreadable') return { kind: 'unreadable' };
    const answer = await this.ask();
    if (answer !== 'ok') return { kind: answer };
    if (!sameBinding(stored, binding)) return { kind: 'unreadable' };
    this.unlocked = true;
    return { kind: 'ok' };
  }

  async load(): Promise<LoadResult> {
    const stored = await this.read();
    if (stored === 'empty') return { kind: 'empty' };
    if (stored === 'unreadable') return { kind: 'unreadable' };
    if (!this.unlocked) return { kind: 'locked' };
    return { kind: 'ok', refreshToken: stored.token };
  }

  async store(refreshToken: string, binding: VaultBinding): Promise<StoreResult> {
    const stored = await this.read();
    if (stored === 'empty' || stored === 'unreadable') {
      const answer = await this.ask();
      if (answer !== 'ok') return { kind: answer };
      await this.write(refreshToken, binding);
      this.unlocked = true;
      return { kind: 'ok' };
    }
    if (!this.unlocked || !sameBinding(stored, binding)) return { kind: 'locked' };
    await this.write(refreshToken, binding);
    return { kind: 'ok' };
  }

  async lock(): Promise<void> {
    this.unlocked = false;
  }

  async wipe(): Promise<void> {
    this.unlocked = false;
    await this.storage.delete(TOKEN_KEY);
  }

  private async ask(): Promise<'ok' | 'cancelled' | 'no_screen_lock'> {
    if (!(await this.presence.hasScreenLock())) return 'no_screen_lock';
    return (await this.presence.prompt(this.promptText())) ? 'ok' : 'cancelled';
  }

  private async read(): Promise<Stored | 'empty' | 'unreadable'> {
    const raw = await this.storage.get(TOKEN_KEY);
    if (raw === null) return 'empty';
    try {
      const value = JSON.parse(raw) as Partial<Stored>;
      if (
        typeof value.token === 'string' &&
        typeof value.userId === 'string' &&
        typeof value.installationId === 'string'
      ) {
        return { token: value.token, userId: value.userId, installationId: value.installationId };
      }
    } catch {
      // fall through
    }
    return 'unreadable';
  }

  private write(token: string, binding: VaultBinding): Promise<void> {
    const value: Stored = { token, userId: binding.userId, installationId: binding.installationId };
    return this.storage.set(TOKEN_KEY, JSON.stringify(value));
  }
}

function sameBinding(a: VaultBinding, b: VaultBinding): boolean {
  return a.userId === b.userId && a.installationId === b.installationId;
}
