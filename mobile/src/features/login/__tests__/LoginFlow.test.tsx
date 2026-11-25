import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import type { ApiError } from '../../../api/errors';
import type { LoginResult } from '../../../auth/session';
import { LoginFlow } from '../LoginFlow';
import type { ApiResult, LoginApi, LoginResponse, OtpRequested } from '../loginApi';

const INSTALL = '11111111-aaaa-4bbb-8ccc-000000000001';

const requested = (over: Partial<OtpRequested> = {}): ApiResult<OtpRequested> => ({
  ok: true,
  data: {
    challenge_id: 'ab7c33d8-fc31-4b5f-8f48-624fdc497f53',
    channel: 'whatsapp',
    expires_in: 300,
    resend_after: 60,
    ...over,
  },
});

const login: LoginResponse = {
  session_id: 'abbe778d-812f-4251-bc99-1934ee33aebb',
  access_token: 'chm_at_x',
  access_expires_in: 900,
  refresh_token: 'chm_rt_x',
  refresh_expires_in: 2592000,
  user: { id: '66af8a0f-1830-42fc-90c3-b5864ac56033', is_new: false },
  active_chama_id: null,
  memberships: [],
};

const apiError = (status: number, code: ApiError['code'], extra: Partial<ApiError> = {}) =>
  ({ ok: false, error: { kind: 'api', status, code, ...extra } }) as const;

function fakeApi(over: Partial<LoginApi> = {}) {
  return {
    requestOtp: jest.fn<ReturnType<LoginApi['requestOtp']>, Parameters<LoginApi['requestOtp']>>(async () =>
      requested(),
    ),
    verifyOtp: jest.fn<ReturnType<LoginApi['verifyOtp']>, Parameters<LoginApi['verifyOtp']>>(async () => ({
      ok: true,
      data: login,
    })),
    ...over,
  };
}

async function renderFlow(
  api: LoginApi,
  completeLogin: (l: LoginResponse) => Promise<LoginResult> = async () => ({ kind: 'ok' }),
  extra: { devReadCode?: (phone: string) => Promise<string | null>; notice?: string } = {},
) {
  await render(
    <SafeAreaProvider
      initialMetrics={{
        frame: { x: 0, y: 0, width: 360, height: 640 },
        insets: { top: 0, left: 0, right: 0, bottom: 0 },
      }}
    >
      <LoginFlow api={api} installationId={async () => INSTALL} completeLogin={completeLogin} {...extra} />
    </SafeAreaProvider>,
  );
}

async function enterPhone(text = '0712 345 678') {
  await fireEvent.changeText(screen.getByTestId('phone-input'), text);
  await fireEvent.press(screen.getByRole('button', { name: 'Send code' }));
}

async function enterCode(code = '312080') {
  await fireEvent.changeText(screen.getByTestId('code-input'), code);
  await fireEvent.press(screen.getByRole('button', { name: 'Continue' }));
}

beforeEach(() => jest.useFakeTimers());
afterEach(() => jest.useRealTimers());

describe('S1 sign in: request, code, resend', () => {
  it('normalizes the number, sends the installation id, and says the code came on WhatsApp', async () => {
    const api = fakeApi();
    await renderFlow(api);
    await enterPhone('0712 345 678');
    expect(api.requestOtp).toHaveBeenCalledWith('+254712345678', INSTALL);
    expect(screen.getByText('We sent a code on WhatsApp.')).toBeOnTheScreen();
  });

  it('says "by SMS" when the server used SMS', async () => {
    await renderFlow(fakeApi({ requestOtp: async () => requested({ channel: 'sms' }) }));
    await enterPhone();
    expect(screen.getByText('We sent a code by SMS.')).toBeOnTheScreen();
  });

  it('rejects an invalid number locally without calling the server', async () => {
    const api = fakeApi();
    await renderFlow(api);
    await enterPhone('0712');
    expect(api.requestOtp).not.toHaveBeenCalled();
    expect(screen.getByText('This phone number is not complete or not valid.')).toBeOnTheScreen();
  });

  it('offers "Send a new code" only after the server cooldown, then sends a new code', async () => {
    const api = fakeApi();
    await renderFlow(api);
    await enterPhone();
    expect(screen.getByText('You can ask for a new code in 1 minute.')).toBeOnTheScreen();
    expect(screen.queryByRole('button', { name: 'Send a new code' })).toBeNull();

    await act(async () => {
      jest.advanceTimersByTime(59_000);
    });
    expect(screen.getByText('You can ask for a new code in 1 second.')).toBeOnTheScreen();

    await act(async () => {
      jest.advanceTimersByTime(1_000);
    });
    await fireEvent.press(screen.getByRole('button', { name: 'Send a new code' }));
    expect(api.requestOtp).toHaveBeenCalledTimes(2);
    expect(screen.getByText('You can ask for a new code in 1 minute.')).toBeOnTheScreen();
  });

  it('verifies the code and completes the login', async () => {
    const api = fakeApi();
    const complete = jest.fn(async () => ({ kind: 'ok' }) as const);
    await renderFlow(api, complete);
    await enterPhone();
    await enterCode('312080');
    expect(api.verifyOtp).toHaveBeenCalledWith('ab7c33d8-fc31-4b5f-8f48-624fdc497f53', '312080', INSTALL);
    expect(complete).toHaveBeenCalledWith(login);
  });

  it('a wrong, expired or used code shows one message and clears the field', async () => {
    await renderFlow(fakeApi({ verifyOtp: async () => apiError(400, 'invalid_code') }));
    await enterPhone();
    await enterCode('000000');
    expect(screen.getByText("That code didn't work. Check it or ask for a new one.")).toBeOnTheScreen();
    expect(screen.getByTestId('code-input').props.value).toBe('');
  });

  it('a cancelled "confirm it is you" prompt keeps the user on a retry screen', async () => {
    const complete = jest
      .fn<Promise<LoginResult>, [LoginResponse]>()
      .mockResolvedValueOnce({ kind: 'cancelled' })
      .mockResolvedValueOnce({ kind: 'ok' });
    const api = fakeApi();
    await renderFlow(api, complete);
    await enterPhone();
    await enterCode();
    expect(screen.getByText('Please confirm it is you to finish signing in.')).toBeOnTheScreen();
    await fireEvent.press(screen.getByRole('button', { name: 'Confirm it is me' }));
    expect(complete).toHaveBeenCalledTimes(2);
    expect(api.verifyOtp).toHaveBeenCalledTimes(1); // the code is not re-used
  });

  it('no screen lock at login asks the user to set one', async () => {
    await renderFlow(fakeApi(), async () => ({ kind: 'no_screen_lock' }));
    await enterPhone();
    await enterCode();
    expect(screen.getByText('Set a screen lock to protect your chama records.')).toBeOnTheScreen();
    expect(screen.getByRole('button', { name: 'Open settings' })).toBeOnTheScreen();
  });

  it('shows why the user was signed out', async () => {
    await renderFlow(fakeApi(), undefined, { notice: 'Please sign in again.' });
    expect(screen.getByText('Please sign in again.')).toBeOnTheScreen();
  });
});

describe('S1 rate limits and lockout', () => {
  it('rate_limited shows the server wait, disables sending, and never retries by itself', async () => {
    const api = fakeApi({
      requestOtp: jest.fn(async () => apiError(429, 'rate_limited', { retryAfter: 56 })),
    });
    await renderFlow(api);
    await enterPhone();
    expect(screen.getByText('Please wait 56 seconds before asking for another code.')).toBeOnTheScreen();
    expect(screen.getByRole('button', { name: 'Send code' })).toBeDisabled();

    await act(async () => {
      jest.advanceTimersByTime(120_000);
    });
    expect(api.requestOtp).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Send code' })).toBeEnabled();
  });

  it('locked at verify shows the lockout time and disables Continue', async () => {
    const api = fakeApi({ verifyOtp: jest.fn(async () => apiError(429, 'locked', { retryAfter: 900 })) });
    await renderFlow(api);
    await enterPhone();
    await enterCode();
    expect(screen.getByText('Too many wrong codes. Try again in 15 minutes.')).toBeOnTheScreen();
    await fireEvent.changeText(screen.getByTestId('code-input'), '123456');
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
    await act(async () => {
      jest.advanceTimersByTime(30 * 60_000);
    });
    expect(api.verifyOtp).toHaveBeenCalledTimes(1);
  });

  it('a server-side phone rejection shows as a field error', async () => {
    await renderFlow(
      fakeApi({ requestOtp: async () => apiError(400, 'invalid_phone', { reason: 'not_mobile' }) }),
    );
    await enterPhone('0712 345 678');
    expect(screen.getByText('Use a mobile number. Landlines cannot receive codes.')).toBeOnTheScreen();
  });

  it('no connection is explained in plain words', async () => {
    await renderFlow(fakeApi({ requestOtp: async () => ({ ok: false, error: { kind: 'network' } }) }));
    await enterPhone();
    expect(
      screen.getByText('No internet connection. Check your data or Wi-Fi and try again.'),
    ).toBeOnTheScreen();
  });
});

describe('dev outbox button', () => {
  it('fills the code from the outbox when provided', async () => {
    const devReadCode = jest.fn(async () => '424242');
    await renderFlow(fakeApi(), undefined, { devReadCode });
    await enterPhone();
    await fireEvent.press(screen.getByRole('button', { name: 'Dev: fill code from outbox' }));
    expect(devReadCode).toHaveBeenCalledWith('+254712345678');
    expect(screen.getByTestId('code-input').props.value).toBe('424242');
  });

  it('is absent without a reader (release builds)', async () => {
    await renderFlow(fakeApi());
    await enterPhone();
    expect(screen.queryByRole('button', { name: 'Dev: fill code from outbox' })).toBeNull();
  });
});
