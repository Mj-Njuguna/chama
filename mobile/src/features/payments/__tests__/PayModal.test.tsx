import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';

import type { AuthedCall, CallResult } from '../../../auth/tokenManager';
import type { M2Api, StkRequest, StkStatus } from '../../m2/m2Api';
import { PayModal } from '../PayModal';

const callAuthed = <T,>(call: AuthedCall<T>) => call('chm_at_x');

function stk(status: StkStatus, receipt: string | null = null): StkRequest {
  return {
    stk_request_id: '11111111-1111-1111-1111-111111111111',
    status,
    amount_minor: 150_000,
    mpesa_receipt: receipt,
    created_at: '2026-11-26T08:00:00Z',
  };
}

/** createStk answers `created`; each status read returns the next item of `statuses`. */
function fakeM2(created: CallResult<StkRequest>, statuses: StkStatus[] = []) {
  const amounts: number[] = [];
  let reads = 0;
  const m2 = {
    createStk: (amount: number) => async () => {
      amounts.push(amount);
      return created;
    },
    stkStatus: () => async () => {
      const status = statuses[Math.min(reads, statuses.length - 1)] ?? 'pending';
      reads += 1;
      return { ok: true, status: 200, data: stk(status, status === 'succeeded' ? 'SKL4H2JQ9P' : null) };
    },
  } as unknown as M2Api;
  return { m2, amounts, reads: () => reads };
}

let client: QueryClient;

async function open(m2: M2Api, prefill: number | null = 150_000, maxWaitMs = 5_000) {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  const onClose = jest.fn();
  await render(
    <QueryClientProvider client={client}>
      <PayModal
        visible
        m2={m2}
        callAuthed={callAuthed}
        prefillMinor={prefill}
        onClose={onClose}
        pollMs={5}
        maxWaitMs={maxWaitMs}
      />
    </QueryClientProvider>,
  );
  return onClose;
}

afterEach(() => client.clear());

describe('Pay via M-Pesa', () => {
  it('prefills the server balance, sends it, polls, and shows the receipt on success', async () => {
    const fake = fakeM2({ ok: true, status: 202, data: stk('pending') }, ['pending', 'succeeded']);
    await open(fake.m2);
    expect(screen.getByDisplayValue('1500')).toBeTruthy();
    await fireEvent.press(screen.getByText('Send payment request'));
    expect(await screen.findByTestId('pay-result-succeeded')).toBeTruthy();
    expect(screen.getByText('Payment received. Thank you.')).toBeTruthy();
    expect(screen.getByText('M-Pesa receipt SKL4H2JQ9P')).toBeTruthy();
    expect(fake.amounts).toEqual([150_000]);
    expect(fake.reads()).toBeGreaterThanOrEqual(2);
  });

  it('sends an edited amount in minor units', async () => {
    const fake = fakeM2({ ok: true, status: 202, data: stk('pending') }, ['succeeded']);
    await open(fake.m2);
    await fireEvent.changeText(screen.getByTestId('pay-amount'), '2,000.50');
    await fireEvent.press(screen.getByText('Send payment request'));
    await screen.findByTestId('pay-result-succeeded');
    expect(fake.amounts).toEqual([200_050]);
  });

  it('shows cancelled when the member dismisses the prompt, and offers a retry', async () => {
    const fake = fakeM2({ ok: true, status: 202, data: stk('pending') }, ['cancelled']);
    await open(fake.m2);
    await fireEvent.press(screen.getByText('Send payment request'));
    expect(await screen.findByText('You cancelled the request on your phone.')).toBeTruthy();
    await fireEvent.press(screen.getByText('Try again'));
    expect(screen.getByText('Send payment request')).toBeTruthy();
  });

  it('shows the server timeout status', async () => {
    const fake = fakeM2({ ok: true, status: 202, data: stk('pending') }, ['pending', 'timeout']);
    await open(fake.m2);
    await fireEvent.press(screen.getByText('Send payment request'));
    expect(await screen.findByTestId('pay-result-timeout')).toBeTruthy();
  });

  it('stops polling after the wait limit and says the payment may still arrive', async () => {
    const fake = fakeM2({ ok: true, status: 202, data: stk('pending') }, ['pending']);
    await open(fake.m2, 150_000, 40);
    await fireEvent.press(screen.getByText('Send payment request'));
    expect(await screen.findByTestId('pay-result-timeout')).toBeTruthy();
    expect(
      screen.getByText('No answer from M-Pesa yet. If you paid, it will show in your payments shortly.'),
    ).toBeTruthy();
  });

  it('explains when the chama has not connected M-Pesa', async () => {
    const fake = fakeM2({
      ok: false,
      status: 409,
      error: { kind: 'api', status: 409, code: 'state_conflict', reason: 'payments_not_linked' },
    });
    await open(fake.m2);
    await fireEvent.press(screen.getByText('Send payment request'));
    expect(
      await screen.findByText('Your chama has not connected its M-Pesa account yet. Ask an official.'),
    ).toBeTruthy();
    expect(fake.reads()).toBe(0);
  });

  it('refuses an amount it cannot read without calling the server', async () => {
    const fake = fakeM2({ ok: true, status: 202, data: stk('pending') });
    await open(fake.m2, null);
    await fireEvent.changeText(screen.getByTestId('pay-amount'), '12.345');
    await fireEvent.press(screen.getByText('Send payment request'));
    expect(screen.getByText('Enter an amount in shillings, for example 1500.')).toBeTruthy();
    expect(fake.amounts).toEqual([]);
  });
});
