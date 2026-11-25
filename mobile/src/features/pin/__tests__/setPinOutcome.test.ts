import { setPinOutcome } from '../SetPinScreen';

const api = (status: number, code: string, reason?: string) =>
  ({
    ok: false,
    status,
    error: { kind: 'api', status, code, ...(reason === undefined ? {} : { reason }) },
  }) as unknown as Parameters<typeof setPinOutcome>[0];

describe('setPinOutcome (what the screen says after PUT /auth/pin)', () => {
  it('204 and pin_already_set both continue to the app', () => {
    expect(setPinOutcome({ ok: true, status: 204, data: null })).toBe('done');
    expect(setPinOutcome(api(409, 'pin_already_set'))).toBe('done');
  });

  it.each([
    ['repeated', 'setPin.rejected.repeated'],
    ['sequence', 'setPin.rejected.sequence'],
    ['phone_digits', 'setPin.rejected.phone_digits'],
    ['not_six_digits', 'setPin.rejected.other'],
  ])('pin_rejected (%s) says why', (reason, key) => {
    expect(setPinOutcome(api(400, 'pin_rejected', reason))).toBe(key);
  });

  it('a cancelled code prompt (the sign-in was more than 5 minutes ago) asks to try again', () => {
    expect(setPinOutcome({ ok: false, status: 0, stepUp: 'cancelled' })).toBe('setPin.needsCode');
    expect(setPinOutcome({ ok: false, status: 0, stepUp: 'failed' })).toBe('setPin.failed');
  });

  it('offline needs a connection; anything else is a plain failure', () => {
    expect(setPinOutcome({ ok: false, status: 0, offline: true })).toBe('common.needsConnection');
    expect(setPinOutcome(api(500, 'server_error'))).toBe('setPin.failed');
  });
});
