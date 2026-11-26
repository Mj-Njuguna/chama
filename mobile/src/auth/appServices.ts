import { createApiClient } from '../api/client';
import { createM2Client } from '../api/draftM2';
import { type ChamaApi, createChamaApi } from '../features/chamas/chamaApi';
import { createLoginApi, type LoginApi } from '../features/login/loginApi';
import { createM2Api, type M2Api } from '../features/m2/m2Api';
import { createDeviceStorage, type DeviceStorage } from './createVault';
import type { MonotonicClock } from './monotonicClock';
import { newInstallationId } from './newInstallationId';
import { makeRefreshCall } from './refreshCall';
import { Session } from './session';
import { createSessionApi, type SessionApi } from './sessionApi';
import { createSignedOutChannel, type SignedOutChannel } from './signedOutChannel';
import { createStepUpApi, type StepUpApi } from './stepUpApi';

export type AppServices = {
  readonly session: Session;
  readonly signedOut: SignedOutChannel;
  readonly loginApi: LoginApi;
  readonly sessionApi: SessionApi;
  readonly stepUpApi: StepUpApi;
  readonly chamaApi: ChamaApi;
  /** Phase-2 calls (payments, cycles, ledger views, waivers, feed). */
  readonly m2Api: M2Api;
  /** Idle re-lock clock (monotonic). */
  readonly clock: MonotonicClock;
  /** Development builds only (fake OTP outbox). */
  readonly devReadCode?: (phoneE164: string) => Promise<string | null>;
  /** Development builds only: the newest step-up code for the signed-in user's own number. */
  readonly devReadStepUpCode?: () => Promise<string | null>;
};

/** Composition root. `storage` is injectable for tests. */
export function createAppServices(
  apiBaseUrl: string,
  storage: DeviceStorage = createDeviceStorage(),
): AppServices {
  const client = createApiClient(apiBaseUrl);
  const signedOut = createSignedOutChannel();
  const { vault, identity, clock } = storage;
  const session = new Session({
    vault,
    identity,
    callRefresh: makeRefreshCall(client),
    newInstallationId: () => newInstallationId(),
    onSignedOut: signedOut.emit,
  });
  const sessionApi = createSessionApi(client);
  const services: AppServices = {
    session,
    signedOut,
    loginApi: createLoginApi(client),
    sessionApi,
    stepUpApi: createStepUpApi(client),
    chamaApi: createChamaApi(client),
    m2Api: createM2Api(createM2Client(apiBaseUrl)),
    clock,
  };
  if (__DEV__) {
    // Dev-only: removed from release bundles with the `__DEV__` branch.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { readOutboxCode } = require('../dev/otpOutbox') as typeof import('../dev/otpOutbox');
    return {
      ...services,
      devReadCode: (phone) => readOutboxCode(apiBaseUrl, phone),
      devReadStepUpCode: async () => {
        // The phone stays inside this dev-only helper; screens never see it.
        const me = await session.tokens.callAuthed(sessionApi.me);
        return me.ok ? readOutboxCode(apiBaseUrl, me.data.user.phone) : null;
      },
    };
  }
  return services;
}
