import type { ApiError, StepUpFactor } from '../../api/errors';
import { withStepUp } from '../stepUp';
import type { AuthedResult } from '../tokenManager';

const needs = (factors: StepUpFactor[]): AuthedResult<never> => ({
  ok: false,
  status: 403,
  error: { kind: 'api', status: 403, code: 'step_up_required', factors },
});
const done: AuthedResult<string> = { ok: true, status: 200, data: 'done' };
const apiError = (code: ApiError['code'], status = 403): AuthedResult<never> => ({
  ok: false,
  status,
  error: { kind: 'api', status, code },
});

function sequence(...results: AuthedResult<string>[]) {
  const action = jest.fn(async () => {
    const next = results.shift();
    if (next === undefined) throw new Error('action called too often');
    return next;
  });
  return action;
}

describe('withStepUp', () => {
  it('passes a result through when no step-up is needed', async () => {
    const action = sequence(done);
    const prompt = jest.fn();
    await expect(withStepUp(action, prompt)).resolves.toBe(done);
    expect(prompt).not.toHaveBeenCalled();
  });

  it('prompts for each listed factor in order, then retries once', async () => {
    const action = sequence(needs(['otp', 'pin']), done);
    const asked: StepUpFactor[] = [];
    const result = await withStepUp(action, async (f) => {
      asked.push(f);
      return 'verified';
    });
    expect(asked).toEqual(['otp', 'pin']);
    expect(action).toHaveBeenCalledTimes(2);
    expect(result).toBe(done);
  });

  it('a second step_up_required is a failure, with no further retry', async () => {
    const action = sequence(needs(['pin']), needs(['pin']));
    const prompt = jest.fn(async () => 'verified' as const);
    await expect(withStepUp(action, prompt)).resolves.toEqual({ ok: false, status: 0, stepUp: 'failed' });
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(action).toHaveBeenCalledTimes(2);
  });

  it('cancelling a factor stops: later factors are not asked and the action does not rerun', async () => {
    const action = sequence(needs(['otp', 'pin']));
    const prompt = jest.fn(async () => 'cancelled' as const);
    await expect(withStepUp(action, prompt)).resolves.toEqual({ ok: false, status: 0, stepUp: 'cancelled' });
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(action).toHaveBeenCalledTimes(1);
  });

  it('an empty or unknown factor list fails without prompting', async () => {
    const action = sequence(needs([]));
    const prompt = jest.fn();
    await expect(withStepUp(action, prompt)).resolves.toEqual({ ok: false, status: 0, stepUp: 'failed' });
    expect(prompt).not.toHaveBeenCalled();
  });

  it('other errors on the retry are returned to the caller (pin_not_set, account_frozen)', async () => {
    const frozen = apiError('chama_frozen');
    const action = sequence(needs(['otp']), frozen);
    await expect(withStepUp(action, async () => 'verified')).resolves.toBe(frozen);
    const notSet = apiError('pin_not_set');
    await expect(withStepUp(sequence(notSet), jest.fn())).resolves.toBe(notSet);
  });
});
