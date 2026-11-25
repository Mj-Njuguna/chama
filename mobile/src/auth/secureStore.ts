import * as SecureStore from 'expo-secure-store';

import type { KeyValueStore } from './keyValue';

/**
 * expo-secure-store as plain key-value storage (no `requireAuthentication`: LocalAuthVault does
 * the user-presence part). Values are encrypted at rest by secure-store's own Keystore key.
 * A value that can no longer be decrypted (e.g. copied to a new phone) is deleted and read as
 * missing, which sends the user to login.
 */
export const secureStoreKV: KeyValueStore = {
  async get(key) {
    try {
      return await SecureStore.getItemAsync(key);
    } catch {
      await SecureStore.deleteItemAsync(key).catch(() => undefined);
      return null;
    }
  },
  set: (key, value) => SecureStore.setItemAsync(key, value),
  delete: (key) => SecureStore.deleteItemAsync(key),
};
