import { fireEvent, render, screen } from '@testing-library/react-native';
import { useState } from 'react';
import { Text } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { ApiError, StepUpFactor } from '../../api/errors';
import { Button } from '../../components/ui';
import type { StepUpApi } from '../stepUpApi';
import { StepUpProvider, useStepUp } from '../StepUpProvider';
import type { AuthedCall, AuthedResult, CallResult } from '../tokenManager';

const ok = <T,>(data: T): CallResult<T> => ({ ok: true, status: 200, data });
const fail = (status: number, error: Omit<ApiError, 'kind' | 'status'>): CallResult<never> => ({
  ok: false,
  status,
  error: { kind: 'api', status, ...error },
});
const needs = (factors: StepUpFactor[]) => fail(403, { code: 'step_up_required', factors });
const fresh = (factor: StepUpFactor) => ok({ factor, fresh_for: factor === 'pin' ? 900 : 300 });
const sent = ok({
  challenge_id: 'ab7c33d8-fc31-4b5f-8f48-624fdc497f53',
  channel: 'whatsapp' as const,
  expires_in: 300,
  resend_after: 60,
});

/** Calls go straight through with a fixed access token (the TokenManager is tested elsewhere). */
const tokens = { callAuthed: <T,>(call: AuthedCall<T>): Promise<AuthedResult<T>> => call('chm_at_x') };

function fakeApi(over: { pin?: CallResult<unknown>[]; otp?: CallResult<unknown>[] } = {}) {
  const pins = [...(over.pin ?? [fresh('pin')])];
  const otps = [...(over.otp ?? [fresh('otp')])];
  const sentPins: string[] = [];
  const api: StepUpApi = {
    requestOtp: jest.fn(async () => sent),
    verifyOtp: jest.fn(() => async () => otps.shift() as CallResult<never>),
    verifyPin: jest.fn((pin: string) => async () => {
      sentPins.push(pin);
      return pins.shift() as CallResult<never>;
    }),
    setPin: jest.fn(() => async () => ({ ok: true, status: 204, data: null })),
  };
  return { api, sentPins };
}

/** A screen with one sensitive action: shows what `run` returned. */
function Consumer({ action }: { action: AuthedCall<string> }) {
  const run = useStepUp();
  const [outcome, setOutcome] = useState('none');
  return (
    <>
      <Button
        label="Mark paid"
        onPress={() =>
          void run(action).then((r) =>
            setOutcome(r.ok ? r.data : 'stepUp' in r ? r.stepUp : 'error' in r ? r.error.code : 'other'),
          )
        }
      />
      <Text testID="outcome">{outcome}</Text>
    </>
  );
}

function actionReturning(...results: CallResult<string>[]) {
  return jest.fn<Promise<CallResult<string>>, [string | null]>(async () => {
    const next = results.shift();
    if (next === undefined) throw new Error('action called too often');
    return next;
  });
}

async function setup(action: AuthedCall<string>, api: StepUpApi) {
  await render(
    <SafeAreaProvider
      initialMetrics={{
        frame: { x: 0, y: 0, width: 360, height: 640 },
        insets: { top: 0, left: 0, right: 0, bottom: 0 },
      }}
    >
      <StepUpProvider tokens={tokens} api={api}>
        <Consumer action={action} />
      </StepUpProvider>
    </SafeAreaProvider>,
  );
  await fireEvent.press(screen.getByRole('button', { name: 'Mark paid' }));
}

/** Types the PIN on the keypad; the sixth digit submits it. */
async function enterPin(pin: string) {
  await screen.findByTestId('step-up-pin');
  for (const digit of pin) await fireEvent.press(screen.getByRole('button', { name: digit }));
}

async function enterCode(code: string) {
  await fireEvent.changeText(await screen.findByTestId('step-up-code'), code);
  await fireEvent.press(screen.getByRole('button', { name: 'Confirm' }));
}

describe('step-up prompt and retry', () => {
  it('PIN: prompts, sends the PIN to the server, retries the action once', async () => {
    const action = actionReturning(needs(['pin']), ok('done'));
    const { api, sentPins } = fakeApi();
    await setup(action, api);
    expect(screen.getByText('To protect your account, enter your PIN.')).toBeOnTheScreen();
    await enterPin('275931');
    expect(await screen.findByText('done')).toBeOnTheScreen();
    expect(sentPins).toEqual(['275931']);
    expect(action).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('step-up')).toBeNull();
  });

  it('OTP then PIN, in the order the server lists them', async () => {
    const action = actionReturning(needs(['otp', 'pin']), ok('done'));
    const { api } = fakeApi();
    await setup(action, api);
    expect(await screen.findByTestId('step-up-otp')).toBeOnTheScreen();
    expect(api.requestOtp).toHaveBeenCalledTimes(1);
    await enterCode('312080');
    expect(api.verifyOtp).toHaveBeenCalledWith('ab7c33d8-fc31-4b5f-8f48-624fdc497f53', '312080');
    await enterPin('275931');
    expect(await screen.findByText('done')).toBeOnTheScreen();
  });

  it('a second step_up_required shows an error and does not loop', async () => {
    const action = actionReturning(needs(['pin']), needs(['pin']));
    const { api } = fakeApi();
    await setup(action, api);
    await enterPin('275931');
    expect(await screen.findByText('We could not confirm it is you. Please try again.')).toBeOnTheScreen();
    expect(screen.getByTestId('outcome')).toHaveTextContent('failed');
    expect(action).toHaveBeenCalledTimes(2);
    await fireEvent.press(screen.getByRole('button', { name: 'OK' }));
    expect(screen.queryByTestId('step-up-failed')).toBeNull();
  });

  it('Cancel stops without retrying the action', async () => {
    const action = actionReturning(needs(['pin']));
    const { api } = fakeApi();
    await setup(action, api);
    await fireEvent.press(await screen.findByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('cancelled')).toBeOnTheScreen();
    expect(action).toHaveBeenCalledTimes(1);
    expect(api.verifyPin).not.toHaveBeenCalled();
  });

  it('a wrong PIN shows the tries left and lets the user try again (no action retry yet)', async () => {
    const action = actionReturning(needs(['pin']), ok('done'));
    const { api } = fakeApi({ pin: [fail(400, { code: 'invalid_pin', attemptsLeft: 4 }), fresh('pin')] });
    await setup(action, api);
    await enterPin('111112');
    expect(
      await screen.findByText('That PIN is not correct. Tries left before a pause: 4.'),
    ).toBeOnTheScreen();
    expect(screen.getByLabelText('PIN, 0 of 6 digits entered')).toBeOnTheScreen();
    expect(action).toHaveBeenCalledTimes(1);
    await enterPin('275931');
    expect(await screen.findByText('done')).toBeOnTheScreen();
  });

  it('a PIN lock shows the server wait and disables the keypad', async () => {
    const action = actionReturning(needs(['pin']));
    const { api } = fakeApi({ pin: [fail(429, { code: 'pin_locked', retryAfter: 900 })] });
    await setup(action, api);
    await enterPin('111112');
    expect(await screen.findByText('Too many wrong PINs. Try again in 15 minutes.')).toBeOnTheScreen();
    expect(screen.getByRole('button', { name: '2' })).toBeDisabled();
    expect(api.verifyPin).toHaveBeenCalledTimes(1);
  });

  it('after a lock the server asks for a fresh code first, then the PIN again', async () => {
    const action = actionReturning(needs(['pin']), ok('done'));
    const { api } = fakeApi({ pin: [needs(['otp', 'pin']), fresh('pin')] });
    await setup(action, api);
    await enterPin('275931');
    expect(await screen.findByTestId('step-up-otp')).toBeOnTheScreen();
    await enterCode('312080');
    await enterPin('275931');
    expect(await screen.findByText('done')).toBeOnTheScreen();
    expect(action).toHaveBeenCalledTimes(2);
  });

  it('no PIN set: explains it and offers Cancel only', async () => {
    const action = actionReturning(needs(['pin']));
    const { api } = fakeApi({ pin: [fail(403, { code: 'pin_not_set' })] });
    await setup(action, api);
    await enterPin('275931');
    expect(
      await screen.findByText('You have not set a PIN yet. Set one before doing this.'),
    ).toBeOnTheScreen();
    expect(screen.queryByTestId('step-up-pin')).toBeNull();
    await fireEvent.press(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText('cancelled')).toBeOnTheScreen();
  });

  it('a wrong code clears the field and keeps the prompt open', async () => {
    const action = actionReturning(needs(['otp']), ok('done'));
    const { api } = fakeApi({ otp: [fail(400, { code: 'invalid_code' }), fresh('otp')] });
    await setup(action, api);
    await enterCode('000000');
    expect(
      await screen.findByText("That code didn't work. Check it or ask for a new one."),
    ).toBeOnTheScreen();
    expect(screen.getByTestId('step-up-code').props.value).toBe('');
    await enterCode('312080');
    expect(await screen.findByText('done')).toBeOnTheScreen();
  });

  it('offline while checking the PIN says a connection is needed', async () => {
    const action = actionReturning(needs(['pin']));
    const { api } = fakeApi({ pin: [{ ok: false, status: 0, offline: true }] });
    await setup(action, api);
    await enterPin('275931');
    expect(
      await screen.findByText('This needs an internet connection. Try again when you are online.'),
    ).toBeOnTheScreen();
  });

  it('the PIN is never shown and exactly 6 digits are sent', async () => {
    const action = actionReturning(needs(['pin']), ok('done'));
    const { api, sentPins } = fakeApi();
    await setup(action, api);
    await enterPin('275931');
    expect(await screen.findByText('done')).toBeOnTheScreen();
    expect(sentPins).toEqual(['275931']);
    expect(screen.queryByText('275931')).toBeNull();
  });
});
