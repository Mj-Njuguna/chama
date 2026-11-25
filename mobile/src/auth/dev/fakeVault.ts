import type { LoadResult, SecureVault, StoreResult, UnlockResult, VaultBinding } from '../vault';

/** The simulated system prompt's answer. */
export type FakePrompt = 'approve' | 'cancel' | 'no_screen_lock';

/** TESTS ONLY. Keeps the refresh token in JavaScript memory and simulates the system prompt. */
export class FakeVault implements SecureVault {
  /** Answer for every prompt until changed. */
  prompt: FakePrompt = 'approve';
  /** Number of prompts shown, so tests can prove rotation never prompts. */
  prompts = 0;

  private stored: { token: string; binding: VaultBinding } | null = null;
  private unlocked = false;
  /** Simulates the screen lock being removed: stored data becomes unreadable. */
  invalidated = false;

  async unlock(binding: VaultBinding): Promise<UnlockResult> {
    if (this.stored === null) return { kind: 'empty' };
    const answer = this.ask();
    if (answer !== 'ok') return { kind: answer };
    if (this.invalidated || !sameBinding(this.stored.binding, binding)) return { kind: 'unreadable' };
    this.unlocked = true;
    return { kind: 'ok' };
  }

  async load(): Promise<LoadResult> {
    if (this.stored === null) return { kind: 'empty' };
    if (this.invalidated) return { kind: 'unreadable' };
    if (!this.unlocked) return { kind: 'locked' };
    return { kind: 'ok', refreshToken: this.stored.token };
  }

  async store(refreshToken: string, binding: VaultBinding): Promise<StoreResult> {
    if (this.stored === null || this.invalidated) {
      // First store after login: one "confirm it's you" prompt creates the key.
      const answer = this.ask();
      if (answer !== 'ok') return { kind: answer };
      this.invalidated = false;
      this.stored = { token: refreshToken, binding };
      this.unlocked = true;
      return { kind: 'ok' };
    }
    if (!this.unlocked || !sameBinding(this.stored.binding, binding)) return { kind: 'locked' };
    this.stored = { token: refreshToken, binding };
    return { kind: 'ok' };
  }

  async lock(): Promise<void> {
    this.unlocked = false;
  }

  async wipe(): Promise<void> {
    this.stored = null;
    this.unlocked = false;
    this.invalidated = false;
  }

  private ask(): 'ok' | 'cancelled' | 'no_screen_lock' {
    this.prompts += 1;
    if (this.prompt === 'cancel') return 'cancelled';
    if (this.prompt === 'no_screen_lock') return 'no_screen_lock';
    return 'ok';
  }
}

function sameBinding(a: VaultBinding, b: VaultBinding): boolean {
  return a.userId === b.userId && a.installationId === b.installationId;
}
