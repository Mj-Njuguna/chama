import { type ConfigResult, parseConfig } from './config';

export type { AppConfig, ConfigErrorCode, ConfigResult } from './config';

let cached: ConfigResult | undefined;

/** The validated app config. `process.env.EXPO_PUBLIC_*` must be read literally to be inlined. */
export function loadConfig(): ConfigResult {
  cached ??= parseConfig(process.env.EXPO_PUBLIC_API_BASE_URL, __DEV__);
  return cached;
}
