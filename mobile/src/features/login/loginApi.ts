import { Platform } from 'react-native';

import type { ApiClient, Schemas } from '../../api/client';
import { type ApiError, type NetworkError, toApiError } from '../../api/errors';

export type OtpRequested = Schemas['OtpRequested'];
export type LoginResponse = Schemas['Login'];

export type ApiResult<T> =
  { readonly ok: true; readonly data: T } | { readonly ok: false; readonly error: ApiError | NetworkError };

/** The two unauthenticated login calls. Screens depend on this interface; tests fake it. */
export interface LoginApi {
  requestOtp(phoneE164: string, installationId: string): Promise<ApiResult<OtpRequested>>;
  verifyOtp(challengeId: string, code: string, installationId: string): Promise<ApiResult<LoginResponse>>;
}

/** Only the model name is sent as the device name, at most 64 characters. */
export function deviceName(): string | undefined {
  const model = (Platform.constants as { Model?: unknown }).Model;
  return typeof model === 'string' && model.trim() !== '' ? model.trim().slice(0, 64) : undefined;
}

export function createLoginApi(client: ApiClient): LoginApi {
  return {
    async requestOtp(phoneE164, installationId) {
      try {
        const { data, error, response } = await client.POST('/api/v1/auth/otp/request', {
          body: { phone: phoneE164, device_id: installationId },
        });
        return data !== undefined
          ? { ok: true, data }
          : { ok: false, error: toApiError(response.status, error) };
      } catch {
        return { ok: false, error: { kind: 'network' } };
      }
    },
    async verifyOtp(challengeId, code, installationId) {
      const name = deviceName();
      try {
        const { data, error, response } = await client.POST('/api/v1/auth/otp/verify', {
          body: {
            challenge_id: challengeId,
            code,
            // `name` defaults to "" on the server; openapi-typescript marks defaulted fields required.
            device: { installation_id: installationId, platform: 'android', name: name ?? '' },
          },
        });
        return data !== undefined
          ? { ok: true, data }
          : { ok: false, error: toApiError(response.status, error) };
      } catch {
        return { ok: false, error: { kind: 'network' } };
      }
    },
  };
}
