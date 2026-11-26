import type { Schemas } from './client';

/** The generated error body. */
type ErrorDetail = Schemas['ErrorDetail'];

/** Every code the API can return (generated enum). Switch on it; never on `message`. */
export type ErrorCode = Schemas['ErrorCodeEnum'];
/** Sub-codes for invalid_phone, pin_rejected and state_conflict (generated enum). */
export type ErrorReason = Schemas['ErrorReasonEnum'];
/** Factors a `step_up_required` answer can list (generated enum). */
export type StepUpFactor = Schemas['StepUpFactorEnum'];

/** Code used for an error body without the contract shape (e.g. a proxy's HTML 502) or a code
 * this build does not know yet (the contract allows new codes to be added). */
export const UNRECOGNIZED_ERROR = 'unrecognized_error';

/** Runtime copy of the generated enums. The `Exact` checks fail typecheck if the schema and
 * these lists ever differ (after `npm run gen:api`). */
const ERROR_CODES = [
  'validation_error',
  'malformed_request',
  'not_authenticated',
  'permission_denied',
  'not_found',
  'method_not_allowed',
  'unsupported_media_type',
  'rate_limited',
  'invalid_phone',
  'invalid_code',
  'locked',
  'service_unavailable',
  'invalid_token',
  'token_expired',
  'session_revoked',
  'session_expired',
  'refresh_reused',
  'chama_not_available',
  'step_up_required',
  'cooling_off',
  'chama_frozen',
  'pin_not_set',
  'pin_already_set',
  'pin_rejected',
  'invalid_pin',
  'pin_locked',
  'state_conflict',
] as const;
const ERROR_REASONS = [
  'invalid_format',
  'invalid_number',
  'country_not_allowed',
  'not_mobile',
  'not_six_digits',
  'repeated',
  'sequence',
  'phone_digits',
  'duplicate_receipt',
  'member_not_active',
  'payments_not_linked',
  'payment_already_allocated',
  'linking_changed',
  'fine_not_open',
  'waiver_pending',
  'waiver_decided',
  'same_official',
] as const;

type Exact<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const codesMatchSchema: Exact<(typeof ERROR_CODES)[number], ErrorCode> = true;
const reasonsMatchSchema: Exact<(typeof ERROR_REASONS)[number], ErrorReason> = true;
void codesMatchSchema;
void reasonsMatchSchema;

const STEP_UP_FACTORS = ['otp', 'pin'] as const;
const factorsMatchSchema: Exact<(typeof STEP_UP_FACTORS)[number], StepUpFactor> = true;
void factorsMatchSchema;
const KNOWN_FACTORS: ReadonlySet<string> = new Set(STEP_UP_FACTORS);

const KNOWN_CODES: ReadonlySet<string> = new Set(ERROR_CODES);
const KNOWN_REASONS: ReadonlySet<string> = new Set(ERROR_REASONS);

/** The app's view of a failed call. `code` comes from the server; never show `message`. */
export type ApiError = {
  readonly kind: 'api';
  readonly status: number;
  readonly code: ErrorCode | typeof UNRECOGNIZED_ERROR;
  readonly retryAfter?: number;
  readonly reason?: ErrorReason;
  /** With invalid_pin: wrong PINs left before a lock. */
  readonly attemptsLeft?: number;
  /** With step_up_required: factors to verify, in order. Any unknown factor empties the list. */
  readonly factors?: readonly StepUpFactor[];
  readonly fieldErrors?: ErrorDetail['field_errors'];
};

/** No HTTP response at all (offline, DNS, timeout, connection reset). */
export type NetworkError = { readonly kind: 'network' };

/** Maps an error response body to an ApiError. The body is untrusted: checked at runtime. */
export function toApiError(status: number, body: unknown): ApiError {
  const detail = typeof body === 'object' && body !== null ? (body as { error?: unknown }).error : undefined;
  if (typeof detail !== 'object' || detail === null) {
    return { kind: 'api', status, code: UNRECOGNIZED_ERROR };
  }
  const {
    code,
    retry_after: retryAfter,
    reason,
    attempts_left: attemptsLeft,
    factors,
    field_errors: fieldErrors,
  } = detail as Record<string, unknown>;
  if (typeof code !== 'string' || !KNOWN_CODES.has(code)) {
    return { kind: 'api', status, code: UNRECOGNIZED_ERROR };
  }
  return {
    kind: 'api',
    status,
    code: code as ErrorCode,
    ...(typeof retryAfter === 'number' && Number.isSafeInteger(retryAfter) && retryAfter >= 0
      ? { retryAfter }
      : {}),
    ...(typeof reason === 'string' && KNOWN_REASONS.has(reason) ? { reason: reason as ErrorReason } : {}),
    ...(typeof attemptsLeft === 'number' && Number.isSafeInteger(attemptsLeft) && attemptsLeft >= 0
      ? { attemptsLeft }
      : {}),
    ...(Array.isArray(factors) ? { factors: parseFactors(factors) } : {}),
    ...(typeof fieldErrors === 'object' && fieldErrors !== null
      ? { fieldErrors: fieldErrors as NonNullable<ErrorDetail['field_errors']> }
      : {}),
  };
}

/** All or nothing: a list with any unknown factor is treated as empty (the app cannot satisfy it). */
function parseFactors(raw: readonly unknown[]): readonly StepUpFactor[] {
  return raw.every((f) => typeof f === 'string' && KNOWN_FACTORS.has(f)) ? (raw as StepUpFactor[]) : [];
}
