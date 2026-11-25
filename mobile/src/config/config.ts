/**
 * Runtime configuration, built from public EXPO_PUBLIC_* values (inlined at bundle time).
 * Feature code imports `getConfig()` from `src/config`; it never reads process.env or
 * hard-codes a URL.
 */

export type AppConfig = {
  /** Backend origin without a trailing slash, e.g. `http://10.0.2.2:8000`. API paths carry `/api/v1`. */
  readonly apiBaseUrl: string;
  /** True only in development (debug) builds. */
  readonly isDev: boolean;
};

export type ConfigErrorCode = 'missing_api_base_url' | 'invalid_api_base_url' | 'insecure_api_base_url';

export type ConfigResult =
  { readonly ok: true; readonly config: AppConfig } | { readonly ok: false; readonly error: ConfigErrorCode };

// scheme://host[:port][/path], no query, fragment, credentials or whitespace. A regex rather than
// `URL`, whose React Native polyfill does not implement every property.
const BASE_URL = /^(https?):\/\/([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:\d{1,5})?(\/[A-Za-z0-9._~\-/]*)?$/;

/**
 * Validates the API base URL. Release builds accept only https: release builds also refuse
 * cleartext at the OS level (debug-only `usesCleartextTraffic`), and this makes a misconfigured
 * release fail with a clear error instead of a silent network failure.
 */
export function parseConfig(rawBaseUrl: string | undefined, isDev: boolean): ConfigResult {
  const value = (rawBaseUrl ?? '').trim();
  if (value === '') {
    return { ok: false, error: 'missing_api_base_url' };
  }
  const match = BASE_URL.exec(value);
  if (match === null) {
    return { ok: false, error: 'invalid_api_base_url' };
  }
  if (match[1] !== 'https' && !isDev) {
    return { ok: false, error: 'insecure_api_base_url' };
  }
  return { ok: true, config: { apiBaseUrl: value.replace(/\/+$/, ''), isDev } };
}
