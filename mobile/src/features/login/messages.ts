import type { ApiError } from '../../api/errors';
import { t } from '../../i18n';
import type { PhoneError } from '../../lib/phone';

/**
 * Turns login API errors into plain-language messages. Switches on `code` only; the
 * server's `message` is never shown. Waits are shown, never auto-retried.
 */

/** "56 seconds", "15 minutes", "1 hour" (rounded up: never tell the user to retry too early). */
export function formatWait(seconds: number): string {
  const s = Math.max(1, Math.ceil(seconds));
  if (s < 60) return t(s === 1 ? 'time.second' : 'time.seconds', { count: s });
  const minutes = Math.ceil(s / 60);
  if (minutes < 60) return t(minutes === 1 ? 'time.minute' : 'time.minutes', { count: minutes });
  const hours = Math.ceil(minutes / 60);
  return t(hours === 1 ? 'time.hour' : 'time.hours', { count: hours });
}

export function phoneErrorMessage(error: PhoneError): string {
  return t(`login.phone.error.${error}`);
}

export type LoginErrorView = {
  readonly message: string;
  /** Seconds until the user may try again (rate_limited, locked); the button stays disabled. */
  readonly waitSeconds?: number;
  /** True when the code field itself is wrong (clear it, keep the user on the OTP screen). */
  readonly codeRejected?: boolean;
  /** True when the phone field is wrong (show as a field error). */
  readonly phoneRejected?: boolean;
};

export function loginErrorView(error: ApiError | { kind: 'network' }): LoginErrorView {
  if (error.kind === 'network') return { message: t('login.error.offline') };
  const wait = error.retryAfter;
  switch (error.code) {
    case 'invalid_code':
      return { message: t('login.otp.error.invalid_code'), codeRejected: true };
    case 'locked':
      return wait === undefined
        ? { message: t('login.error.locked_unknown') }
        : { message: t('login.error.locked', { time: formatWait(wait) }), waitSeconds: wait };
    case 'rate_limited':
      return wait === undefined
        ? { message: t('login.error.rate_limited_unknown') }
        : { message: t('login.error.rate_limited', { time: formatWait(wait) }), waitSeconds: wait };
    case 'invalid_phone': {
      const reason = error.reason;
      const known: PhoneError[] = ['invalid_format', 'invalid_number', 'country_not_allowed', 'not_mobile'];
      return {
        message: phoneErrorMessage(
          known.includes(reason as PhoneError) ? (reason as PhoneError) : 'invalid_number',
        ),
        phoneRejected: true,
      };
    }
    case 'service_unavailable':
      return { message: t('login.error.service_unavailable') };
    default:
      return { message: t('login.error.generic') };
  }
}
