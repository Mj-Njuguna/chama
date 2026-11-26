import type { ApiError } from '../../api/errors';
import type { StepUpResult } from '../../auth/stepUp';
import type { RunWithStepUp } from '../../auth/StepUpProvider';
import type { AuthedResult } from '../../auth/tokenManager';
import { type MessageKey, t } from '../../i18n';
import type { ChamaApi, Role } from '../chamas/chamaApi';
import type { CallAuthed } from '../shell/useAuthedQuery';
import type { M2Api } from './m2Api';

/** What the phase-2 screens need: the APIs, the session's call helpers and who is looking. */
export type Deps = {
  readonly chamaApi: ChamaApi;
  readonly m2: M2Api;
  readonly callAuthed: CallAuthed;
  readonly runWithStepUp: RunWithStepUp;
  readonly chamaId: string;
  readonly role: Role;
  /** The signed-in user's id (from /me), to tell "my" rows apart. */
  readonly myUserId: string;
};

/**
 * Plain message for a failed write; `null` when it worked or when nothing should be shown
 * (signed out: the login screen takes over; step-up cancelled or failed: the prompt handled it).
 * `known` maps specific API errors to a screen's own wording.
 */
export function writeFailure<T>(
  result: AuthedResult<T> | StepUpResult<T>,
  known?: (error: ApiError) => MessageKey | null,
): string | null {
  if (result.ok) return null;
  if ('stepUp' in result || 'signedOut' in result) return null;
  if ('offline' in result) return t('common.needsConnection');
  if ('rateLimited' in result) return t('pay.rateLimited');
  if ('error' in result) {
    if (result.error.code === 'rate_limited') return t('pay.rateLimited');
    const key = known?.(result.error) ?? null;
    if (key !== null) return t(key);
  }
  return t('common.failed');
}
