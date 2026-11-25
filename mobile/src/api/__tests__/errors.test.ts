import { toApiError, UNRECOGNIZED_ERROR } from '../errors';

describe('toApiError', () => {
  it('maps the contract error shape, including retry_after', () => {
    expect(
      toApiError(429, { error: { code: 'rate_limited', message: 'Too many requests.', retry_after: 56 } }),
    ).toEqual({ kind: 'api', status: 429, code: 'rate_limited', retryAfter: 56 });
  });

  it('keeps reason and field_errors, drops message', () => {
    const error = toApiError(400, {
      error: {
        code: 'invalid_phone',
        message: 'x',
        reason: 'not_mobile',
        field_errors: { phone: ['bad'] },
      },
    });
    expect(error).toEqual({
      kind: 'api',
      status: 400,
      code: 'invalid_phone',
      reason: 'not_mobile',
      fieldErrors: { phone: ['bad'] },
    });
    expect(error).not.toHaveProperty('message');
  });

  it.each([
    null,
    'Bad Gateway',
    {},
    { error: 'x' },
    { error: { message: 'no code' } },
    { error: { code: 5 } },
    { error: { code: 'brand_new_code', message: 'from a newer server' } },
  ])('treats a body without the contract shape as unrecognized (%p)', (body) => {
    expect(toApiError(502, body)).toEqual({ kind: 'api', status: 502, code: UNRECOGNIZED_ERROR });
  });

  it.each([-1, 1.5, '10'])('ignores an invalid retry_after (%p)', (retryAfter) => {
    expect(
      toApiError(429, { error: { code: 'rate_limited', message: '', retry_after: retryAfter } }),
    ).toEqual({
      kind: 'api',
      status: 429,
      code: 'rate_limited',
    });
  });

  it('drops a reason this build does not know', () => {
    expect(
      toApiError(400, { error: { code: 'invalid_phone', message: '', reason: 'future_reason' } }),
    ).toEqual({
      kind: 'api',
      status: 400,
      code: 'invalid_phone',
    });
  });

  it('keeps step-up factors in order and attempts_left', () => {
    expect(
      toApiError(403, { error: { code: 'step_up_required', message: '', factors: ['otp', 'pin'] } }),
    ).toEqual({ kind: 'api', status: 403, code: 'step_up_required', factors: ['otp', 'pin'] });
    expect(toApiError(400, { error: { code: 'invalid_pin', message: '', attempts_left: 4 } })).toEqual({
      kind: 'api',
      status: 400,
      code: 'invalid_pin',
      attemptsLeft: 4,
    });
  });

  it('empties a factor list containing a factor this build does not know', () => {
    expect(
      toApiError(403, { error: { code: 'step_up_required', message: '', factors: ['otp', 'passkey'] } }),
    ).toEqual({ kind: 'api', status: 403, code: 'step_up_required', factors: [] });
  });

  it('drops a malformed attempts_left', () => {
    expect(toApiError(400, { error: { code: 'invalid_pin', message: '', attempts_left: -1 } })).toEqual({
      kind: 'api',
      status: 400,
      code: 'invalid_pin',
    });
  });
});
