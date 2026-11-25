import * as LocalAuthentication from 'expo-local-authentication';

import { t } from '../i18n';
import { type DeviceIdentityStore, KeyValueDeviceIdentityStore } from './deviceIdentity';
import { LocalAuthVault, type UserPresence } from './localAuthVault';
import { createMonotonicClock, type MonotonicClock } from './monotonicClock';
import { secureStoreKV } from './secureStore';
import type { SecureVault } from './vault';

export type DeviceStorage = {
  readonly vault: SecureVault;
  readonly identity: DeviceIdentityStore;
  /** Idle re-lock clock (monotonic). */
  readonly clock: MonotonicClock;
};

/** Biometrics or the device screen lock, through expo-local-authentication. */
export const systemPresence: UserPresence = {
  async hasScreenLock() {
    const level = await LocalAuthentication.getEnrolledLevelAsync();
    return level !== LocalAuthentication.SecurityLevel.NONE;
  },
  async prompt({ title, cancel }) {
    const result = await LocalAuthentication.authenticateAsync({
      promptMessage: title,
      cancelLabel: cancel,
      disableDeviceFallback: false,
    });
    return result.success;
  },
};

/** The app's vault, device identity and idle clock. */
export function createDeviceStorage(presence: UserPresence = systemPresence): DeviceStorage {
  return {
    vault: new LocalAuthVault(secureStoreKV, presence, () => ({
      title: t('unlock.prompt.title'),
      cancel: t('unlock.prompt.cancel'),
    })),
    identity: new KeyValueDeviceIdentityStore(secureStoreKV),
    clock: createMonotonicClock(),
  };
}
