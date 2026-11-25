import { formatWait, loginErrorView } from '../features/login/messages';
import { t } from '../i18n';
import type { AuthedResult } from './tokenManager';

type Failure = Exclude<AuthedResult<unknown>, { ok: true }>;

export type StepUpErrorView = {
  readonly message: string;
  /** Seconds before another try is allowed; the submit button stays disabled. */
  readonly waitSeconds?: number;
  /** Nothing the user can type will help (no PIN set, account frozen): offer Cancel only. */
  readonly final?: boolean;
  /** The server wants a fresh code before the next PIN try (after a PIN lock ended). */
  readonly needsOtp?: boolean;
  /** The session ended or refresh is limited: the prompt closes; the session provider takes over. */
  readonly close?: boolean;
  /** The typed code was wrong: clear the field. */
  readonly codeRejected?: boolean;
};

/** Plain-language view of a failed POST /auth/step-up/pin. Switches on `code` only. */
export function pinErrorView(result: Failure): StepUpErrorView {
  if ('offline' in result) return { message: t('stepUp.offline') };
  if ('signedOut' in result || 'rateLimited' in result) return { message: '', close: true };
  const { error } = result;
  const wait = error.retryAfter;
  switch (error.code) {
    case 'invalid_pin':
      return {
        message:
          error.attemptsLeft === undefined
            ? t('stepUp.pin.invalid')
            : t('stepUp.pin.invalidLeft', { count: error.attemptsLeft }),
      };
    case 'pin_locked':
      return wait === undefined
        ? { message: t('stepUp.pin.lockedUnknown'), final: true }
        : { message: t('stepUp.pin.locked', { time: formatWait(wait) }), waitSeconds: wait };
    case 'rate_limited':
      return wait === undefined
        ? { message: t('login.error.rate_limited_unknown') }
        : { message: t('stepUp.rateLimited', { time: formatWait(wait) }), waitSeconds: wait };
    case 'step_up_required':
      return error.factors?.includes('otp') === true
        ? { message: '', needsOtp: true }
        : { message: t('stepUp.failed'), final: true };
    case 'pin_not_set':
      return { message: t('stepUp.pin.notSet'), final: true };
    case 'chama_frozen':
      return { message: t('stepUp.frozen'), final: true };
    default:
      return { message: t('login.error.generic') };
  }
}

/** Plain-language view of a failed step-up code request or check (same codes as login). */
export function otpErrorView(result: Failure): StepUpErrorView {
  if ('offline' in result) return { message: t('stepUp.offline') };
  if ('signedOut' in result || 'rateLimited' in result) return { message: '', close: true };
  if (result.error.code === 'chama_frozen') return { message: t('stepUp.frozen'), final: true };
  const view = loginErrorView(result.error);
  return {
    message: view.message,
    ...(view.waitSeconds === undefined ? {} : { waitSeconds: view.waitSeconds }),
    ...(view.codeRejected === true ? { codeRejected: true } : {}),
  };
}
