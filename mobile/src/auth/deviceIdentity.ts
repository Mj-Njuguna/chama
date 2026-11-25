import type { KeyValueStore } from './keyValue';

/**
 * Non-secret device identity: the `installation_id` sent at login and the signed-in user's id
 * (the vault binding). Neither is a credential; the refresh token lives only in the SecureVault.
 *
 * installation_id rules:
 * - created once and re-used on later logins;
 * - user logout / logout-all KEEPS it (the next login re-uses this device);
 * - a terminal wipe (session_revoked, refresh_reused, session_expired, invalid_token, unreadable
 *   vault) DROPS it, so the next login creates a new device identity.
 */
export interface DeviceIdentityStore {
  getInstallationId(): Promise<string | null>;
  setInstallationId(installationId: string): Promise<void>;
  getUserId(): Promise<string | null>;
  setUserId(userId: string): Promise<void>;
  clear(what: 'user' | 'all'): Promise<void>;
}

/** `[A-Za-z0-9_-]{8,64}` per the contract; a UUID fits. */
export const INSTALLATION_ID = /^[A-Za-z0-9_-]{8,64}$/;

/** Returns the stored installation_id, creating (and storing) a new one if there is none. */
export async function ensureInstallationId(store: DeviceIdentityStore, newId: () => string): Promise<string> {
  const existing = await store.getInstallationId();
  if (existing !== null && INSTALLATION_ID.test(existing)) return existing;
  const created = newId();
  if (!INSTALLATION_ID.test(created)) {
    throw new Error('Generated installation_id does not match the contract format');
  }
  await store.setInstallationId(created);
  return created;
}

/**
 * In-memory store, for tests.
 */
export class MemoryDeviceIdentityStore implements DeviceIdentityStore {
  installationId: string | null = null;
  userId: string | null = null;

  async getInstallationId(): Promise<string | null> {
    return this.installationId;
  }

  async setInstallationId(installationId: string): Promise<void> {
    this.installationId = installationId;
  }

  async getUserId(): Promise<string | null> {
    return this.userId;
  }

  async setUserId(userId: string): Promise<void> {
    this.userId = userId;
  }

  async clear(what: 'user' | 'all'): Promise<void> {
    this.userId = null;
    if (what === 'all') this.installationId = null;
  }
}

const IDENTITY_KEYS = {
  installationId: 'chm.identity.installationId',
  userId: 'chm.identity.userId',
} as const;

/** Persistent store (expo-secure-store in the app), so a stored session survives a restart. */
export class KeyValueDeviceIdentityStore implements DeviceIdentityStore {
  constructor(private readonly storage: KeyValueStore) {}

  getInstallationId(): Promise<string | null> {
    return this.storage.get(IDENTITY_KEYS.installationId);
  }

  setInstallationId(installationId: string): Promise<void> {
    return this.storage.set(IDENTITY_KEYS.installationId, installationId);
  }

  getUserId(): Promise<string | null> {
    return this.storage.get(IDENTITY_KEYS.userId);
  }

  setUserId(userId: string): Promise<void> {
    return this.storage.set(IDENTITY_KEYS.userId, userId);
  }

  async clear(what: 'user' | 'all'): Promise<void> {
    await this.storage.delete(IDENTITY_KEYS.userId);
    if (what === 'all') await this.storage.delete(IDENTITY_KEYS.installationId);
  }
}
