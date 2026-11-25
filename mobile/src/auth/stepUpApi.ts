import type { ApiClient, Schemas } from '../api/client';
import { bearer, runCall } from './apiCall';
import type { AuthedCall, CallResult } from './tokenManager';

export type StepUpOtpSent = Schemas['OtpRequested'];
export type FreshFactor = Schemas['FreshFactor'];

/**
 * Server step-up calls. Freshness lives on the
 * server session; the app keeps nothing (no step-up token). The PIN and the code are sent to the
 * server and never checked or stored by the app.
 */
export type StepUpApi = {
  readonly requestOtp: AuthedCall<StepUpOtpSent>;
  readonly verifyOtp: (challengeId: string, code: string) => AuthedCall<FreshFactor>;
  readonly verifyPin: (pin: string) => AuthedCall<FreshFactor>;
  /**
   * Set the FIRST server PIN (PUT /auth/pin). Needs a fresh code: the sign-in counts for 5 minutes,
   * otherwise the server answers step_up_required ["otp"]. The server checks the PIN rules.
   */
  readonly setPin: (pin: string) => AuthedCall<null>;
};

export function createStepUpApi(client: ApiClient): StepUpApi {
  return {
    requestOtp: (token) =>
      runCall(() => client.POST('/api/v1/auth/step-up/otp/request', { headers: bearer(token) })),
    verifyOtp: (challengeId, code) => (token) =>
      runCall(() =>
        client.POST('/api/v1/auth/step-up/otp/verify', {
          headers: bearer(token),
          body: { challenge_id: challengeId, code },
        }),
      ),
    verifyPin: (pin) => (token) =>
      runCall(() => client.POST('/api/v1/auth/step-up/pin', { headers: bearer(token), body: { pin } })),
    setPin: (pin) => (token) =>
      runCall(() => client.PUT('/api/v1/auth/pin', { headers: bearer(token), body: { pin } })) as Promise<
        CallResult<null>
      >,
  };
}
