import { MemoryKeyValueStore } from '../keyValue';
import { LocalAuthVault, type UserPresence } from '../localAuthVault';

const ME = { userId: 'user-1', installationId: 'install-1' };
const OTHER = { userId: 'user-2', installationId: 'install-1' };

function setup() {
  const storage = new MemoryKeyValueStore();
  const presence = { screenLock: true, answer: true, prompts: 0 };
  const userPresence: UserPresence = {
    hasScreenLock: async () => presence.screenLock,
    prompt: async () => {
      presence.prompts += 1;
      return presence.answer;
    },
  };
  const vault = new LocalAuthVault(storage, userPresence, () => ({ title: 'Unlock', cancel: 'Cancel' }));
  return { storage, presence, vault };
}

describe('LocalAuthVault (local unlock)', () => {
  it('first store asks once, then rotation never prompts', async () => {
    const { vault, presence } = setup();
    await expect(vault.store('chm_rt_1', ME)).resolves.toEqual({ kind: 'ok' });
    await expect(vault.store('chm_rt_2', ME)).resolves.toEqual({ kind: 'ok' });
    expect(presence.prompts).toBe(1);
    await expect(vault.load()).resolves.toEqual({ kind: 'ok', refreshToken: 'chm_rt_2' });
  });

  it('a device without a screen lock cannot store or unlock', async () => {
    const { vault, presence } = setup();
    presence.screenLock = false;
    await expect(vault.store('chm_rt_1', ME)).resolves.toEqual({ kind: 'no_screen_lock' });
    expect(presence.prompts).toBe(0);
  });

  it('a cancelled prompt stores nothing and keeps a stored token locked', async () => {
    const { vault, presence } = setup();
    presence.answer = false;
    await expect(vault.store('chm_rt_1', ME)).resolves.toEqual({ kind: 'cancelled' });
    await expect(vault.load()).resolves.toEqual({ kind: 'empty' });

    presence.answer = true;
    await vault.store('chm_rt_1', ME);
    await vault.lock();
    presence.answer = false;
    await expect(vault.unlock(ME)).resolves.toEqual({ kind: 'cancelled' });
    await expect(vault.load()).resolves.toEqual({ kind: 'locked' });
  });

  it('after lock, unlock prompts once and loads the token', async () => {
    const { vault, presence } = setup();
    await vault.store('chm_rt_1', ME);
    await vault.lock();
    await expect(vault.load()).resolves.toEqual({ kind: 'locked' });
    await expect(vault.store('chm_rt_2', ME)).resolves.toEqual({ kind: 'locked' });
    await expect(vault.unlock(ME)).resolves.toEqual({ kind: 'ok' });
    expect(presence.prompts).toBe(2);
    await expect(vault.load()).resolves.toEqual({ kind: 'ok', refreshToken: 'chm_rt_1' });
  });

  it('a token stored for one user never unlocks for another', async () => {
    const { vault } = setup();
    await vault.store('chm_rt_1', ME);
    await vault.lock();
    await expect(vault.unlock(OTHER)).resolves.toEqual({ kind: 'unreadable' });
  });

  it('corrupt stored data is unreadable; wipe removes it', async () => {
    const { vault, storage } = setup();
    await storage.set('chm.vault.refresh', 'not json');
    await expect(vault.unlock(ME)).resolves.toEqual({ kind: 'unreadable' });
    await vault.wipe();
    await expect(vault.unlock(ME)).resolves.toEqual({ kind: 'empty' });
    expect(storage.data.size).toBe(0);
  });
});
