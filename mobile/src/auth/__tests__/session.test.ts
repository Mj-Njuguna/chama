import { t } from '../../i18n';
import { FakeVault } from '../dev/fakeVault';
import { ensureInstallationId, MemoryDeviceIdentityStore } from '../deviceIdentity';
import { Session } from '../session';
import { type SignedOutReason, signedOutMessageKey } from '../signedOut';
import type { RefreshCallResult } from '../tokenManager';

const USER = '66af8a0f-1830-42fc-90c3-b5864ac56033';
const OTHER_USER = '90bd5ef0-5c87-4608-8334-019471d40913';
const IDS = ['11111111-aaaa-4bbb-8ccc-000000000001', '22222222-aaaa-4bbb-8ccc-000000000002'];

const pair = (n: number) => ({
  session_id: 'abbe778d-812f-4251-bc99-1934ee33aebb',
  access_token: `chm_at_access${n}`,
  access_expires_in: 900,
  refresh_token: `chm_rt_refresh${n}`,
  refresh_expires_in: 2592000,
});

function setup(callRefresh?: (token: string) => Promise<RefreshCallResult>) {
  const vault = new FakeVault();
  const identity = new MemoryDeviceIdentityStore();
  const signedOut: SignedOutReason[] = [];
  let i = 0;
  let n = 0;
  const session = new Session({
    vault,
    identity,
    callRefresh: callRefresh ?? (async () => ({ kind: 'ok', pair: pair(++n) })),
    newInstallationId: () => IDS[i++] ?? 'unexpected',
    onSignedOut: (reason) => signedOut.push(reason),
    now: () => 0,
    sleep: async () => undefined,
  });
  return { vault, identity, session, signedOut };
}

async function loggedIn() {
  const s = setup();
  await s.session.installationId();
  await expect(s.session.completeLogin(pair(0), USER)).resolves.toEqual({ kind: 'ok' });
  return s;
}

describe('FakeVault contract', () => {
  it('rotation never prompts; only unlock and the first store do', async () => {
    const { vault, session } = await loggedIn();
    expect(vault.prompts).toBe(1); // first store: "confirm it's you"
    for (let k = 0; k < 3; k++) await session.tokens.refresh();
    expect(vault.prompts).toBe(1);
    await session.lock();
    await session.unlock();
    expect(vault.prompts).toBe(2);
    await expect(vault.load()).resolves.toEqual({ kind: 'ok', refreshToken: 'chm_rt_refresh3' });
  });

  it('refuses to load while locked', async () => {
    const { vault, session } = await loggedIn();
    await session.lock();
    await expect(vault.load()).resolves.toEqual({ kind: 'locked' });
  });

  it('data bound to one user never unlocks for another', async () => {
    const { vault, identity } = await loggedIn();
    await vault.lock();
    const installationId = (await identity.getInstallationId())!;
    await expect(vault.unlock({ userId: OTHER_USER, installationId })).resolves.toEqual({
      kind: 'unreadable',
    });
  });
});

describe('installation_id lifecycle', () => {
  it('is created once and re-used on later logins', async () => {
    const { session } = setup();
    await expect(session.installationId()).resolves.toBe(IDS[0]);
    await expect(session.installationId()).resolves.toBe(IDS[0]);
  });

  it('user-initiated logout keeps it (the device is re-used)', async () => {
    const { session, vault, signedOut } = await loggedIn();
    await session.logout();
    await expect(vault.load()).resolves.toEqual({ kind: 'empty' });
    expect(session.tokens.hasSession()).toBe(false);
    expect(signedOut).toEqual(['user_logout']);
    await expect(session.installationId()).resolves.toBe(IDS[0]);
  });

  it('a terminal wipe drops it; the next login gets a NEW installation_id', async () => {
    const s = setup(async () => ({
      kind: 'api',
      error: { kind: 'api', status: 401, code: 'refresh_reused' },
    }));
    await s.session.installationId();
    await s.session.completeLogin(pair(0), USER);
    await expect(s.session.tokens.refresh()).resolves.toEqual({
      kind: 'signed_out',
      reason: 'refresh_reused',
    });
    await expect(s.vault.load()).resolves.toEqual({ kind: 'empty' });
    expect(s.signedOut).toEqual(['refresh_reused']);
    await expect(s.session.installationId()).resolves.toBe(IDS[1]);
  });

  it('replaces a stored value that does not match the contract format', async () => {
    const identity = new MemoryDeviceIdentityStore();
    await identity.setInstallationId('bad id!');
    await expect(ensureInstallationId(identity, () => IDS[0]!)).resolves.toBe(IDS[0]);
  });

  it('refuses to store a generated id outside the contract format', async () => {
    await expect(ensureInstallationId(new MemoryDeviceIdentityStore(), () => 'short')).rejects.toThrow(
      /contract format/,
    );
  });
});

describe('Session', () => {
  it('a cancelled first-store prompt starts no session and stores nothing', async () => {
    const { session, vault, identity } = setup();
    vault.prompt = 'cancel';
    await expect(session.completeLogin(pair(0), USER)).resolves.toEqual({ kind: 'cancelled' });
    expect(session.tokens.hasSession()).toBe(false);
    await expect(session.hasStoredSession()).resolves.toBe(false);
    expect(await identity.getUserId()).toBeNull();
    vault.prompt = 'approve';
    await expect(session.completeLogin(pair(0), USER)).resolves.toEqual({ kind: 'ok' });
  });

  it('no screen lock at login is reported, not stored', async () => {
    const { session, vault } = setup();
    vault.prompt = 'no_screen_lock';
    await expect(session.completeLogin(pair(0), USER)).resolves.toEqual({ kind: 'no_screen_lock' });
    expect(session.tokens.hasSession()).toBe(false);
  });

  it('unlock puts only the refresh token in memory; the access token comes from a refresh', async () => {
    const { session } = await loggedIn();
    await session.lock();
    expect(session.tokens.getAccessToken()).toBeNull();
    await expect(session.unlock()).resolves.toEqual({ kind: 'ok' });
    expect(session.tokens.hasSession()).toBe(true);
    expect(session.tokens.getAccessToken()).toBeNull();
  });

  it('a cancelled unlock stays locked and keeps the stored token', async () => {
    const { session, vault } = await loggedIn();
    await session.lock();
    vault.prompt = 'cancel';
    await expect(session.unlock()).resolves.toEqual({ kind: 'cancelled' });
    expect(session.tokens.hasSession()).toBe(false);
    await expect(session.hasStoredSession()).resolves.toBe(true);
    vault.prompt = 'approve';
    await expect(session.unlock()).resolves.toEqual({ kind: 'ok' });
  });

  it('an unreadable vault (screen lock removed) wipes everything and signs out', async () => {
    const { session, vault, signedOut } = await loggedIn();
    await session.lock();
    vault.invalidated = true;
    await expect(session.unlock()).resolves.toEqual({ kind: 'signed_out' });
    expect(signedOut).toEqual(['unreadable_credentials']);
    await expect(session.hasStoredSession()).resolves.toBe(false);
    await expect(session.installationId()).resolves.toBe(IDS[1]);
  });

  it('lock waits for an in-flight refresh so the rotated token is stored first', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const s = setup(async () => {
      await gate;
      return { kind: 'ok', pair: pair(1) };
    });
    await s.session.completeLogin(pair(0), USER);
    const refreshing = s.session.tokens.refresh();
    const locking = s.session.lock();
    release();
    await refreshing;
    await locking;
    await s.session.unlock();
    await expect(s.vault.load()).resolves.toEqual({ kind: 'ok', refreshToken: 'chm_rt_refresh1' });
  });

  it('an unlock that starts while a lock is still waiting for a refresh waits too', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const sent: string[] = [];
    let n = 0;
    const s = setup(async (token) => {
      sent.push(token);
      n += 1;
      if (n === 1) await gate;
      return { kind: 'ok', pair: pair(n) };
    });
    await s.session.completeLogin(pair(0), USER);
    const refreshing = s.session.tokens.refresh();
    const locking = s.session.lock();
    // The unlock screen prompts at once, before the lock has finished.
    const unlocking = s.session.unlock();
    release();
    await refreshing;
    await locking;
    await expect(unlocking).resolves.toEqual({ kind: 'ok' });
    // The unlock loaded the ROTATED token, and the next refresh sends it.
    await s.session.tokens.refresh();
    expect(sent).toEqual(['chm_rt_refresh0', 'chm_rt_refresh1']);
    expect(s.signedOut).toEqual([]);
  });

  it('two locks at once share one lock', async () => {
    const { session, vault } = await loggedIn();
    const lockSpy = jest.spyOn(vault, 'lock');
    await Promise.all([session.lock(), session.lock()]);
    expect(lockSpy).toHaveBeenCalledTimes(1);
  });

  it('a rotated token the vault refuses ends the session instead of failing later', async () => {
    const { session, vault, signedOut } = await loggedIn();
    await vault.lock(); // vault locked behind the session's back
    await expect(session.tokens.refresh()).resolves.toEqual({
      kind: 'signed_out',
      reason: 'invalid_token',
    });
    expect(signedOut).toEqual(['invalid_token']);
    expect(session.tokens.hasSession()).toBe(false);
  });
});

describe('signed-out messages', () => {
  it('refresh_reused (reconnect / grace-window sharp edge) gets a calm explanation', () => {
    const message = t(signedOutMessageKey('refresh_reused')!);
    expect(message).toMatch(/for your security/i);
    expect(message).toMatch(/weak connection/i);
    expect(message).toMatch(/sign in again/i);
    expect(message).not.toMatch(/stolen|theft|attack|hack/i);
  });

  it.each(['session_revoked', 'session_expired', 'invalid_token', 'unreadable_credentials'] as const)(
    '%s explains that the user must sign in again',
    (reason) => {
      expect(t(signedOutMessageKey(reason)!)).toMatch(/sign in again/i);
    },
  );

  it('a deliberate logout needs no message', () => {
    expect(signedOutMessageKey('user_logout')).toBeNull();
  });
});
