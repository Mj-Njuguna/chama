import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';

import type { AuthedCall } from '../../../auth/tokenManager';
import type { ChamaApi, ChamaHome } from '../../chamas/chamaApi';
import { useChamaUi } from '../../chamas/store';
import type { M2Api } from '../../m2/m2Api';
import { HomeScreen } from '../HomeScreen';

function apiReturning(home: ChamaHome | 'offline'): ChamaApi {
  const call: AuthedCall<ChamaHome> = async () =>
    home === 'offline' ? { ok: false, status: 0, offline: true } : { ok: true, status: 200, data: home };
  return {
    home: call,
    me: async () => ({ ok: false, status: 0, offline: true }),
    create: () => async () => ({ ok: false, status: 0, offline: true }),
    switchTo: () => async () => ({ ok: false, status: 0, offline: true }),
    members: async () => ({ ok: false, status: 0, offline: true }),
    createReceipt: () => async () => ({ ok: false, status: 0, offline: true }),
  };
}

const offline = async () => ({ ok: false as const, status: 0 as const, offline: true as const });
const m2 = { collection: offline } as unknown as M2Api;

const callAuthed = <T,>(call: AuthedCall<T>) => call('chm_at_x');

let client: QueryClient;

async function renderHome(api: ChamaApi) {
  // Infinite gcTime: no cache timers left running after the test.
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  await render(
    <QueryClientProvider client={client}>
      <HomeScreen api={api} m2={m2} callAuthed={callAuthed} chamaId="chama-1" onPay={() => undefined} />
    </QueryClientProvider>,
  );
}

const member: NonNullable<ChamaHome['member']> = {
  savings_minor: 1_500_050,
  cycle_status: 'due',
  cycle_due_date: '2026-12-05',
  cycle_balance_minor: 500_000,
  active_loan_balance_minor: null,
};

beforeEach(() => useChamaUi.getState().reset());
afterEach(() => client.clear());

describe('home money display', () => {
  it('member: formats server minor units, never computes them', async () => {
    await renderHome(apiReturning({ role: 'member', member, official: null }));
    expect(await screen.findByText('KES 15,000.50')).toBeTruthy();
    expect(screen.getByText('KES 5,000')).toBeTruthy();
    // A value the server does not compute yet is labelled, not shown as zero.
    expect(screen.getByText('Not available yet')).toBeTruthy();
    expect(screen.queryByTestId('official-home')).toBeNull();
  });

  it('official: group view first, with a switch to their own account', async () => {
    await renderHome(
      apiReturning({
        role: 'treasurer',
        member,
        official: {
          liquidity_minor: 25_000_000,
          collection_rate_percent: 80,
          pending_approvals: 2,
          overdue_count: 3,
        },
      }),
    );
    expect(await screen.findByText('KES 250,000')).toBeTruthy();
    expect(screen.getAllByText('80% collected').length).toBeGreaterThan(0);
    await fireEvent.press(screen.getByRole('tab', { name: 'My account' }));
    expect(await screen.findByText('KES 15,000.50')).toBeTruthy();
  });

  it('offline with nothing cached: a clear message, no figures', async () => {
    await renderHome(apiReturning('offline'));
    expect(await screen.findByText('You are offline. Connect to the internet to load this.')).toBeTruthy();
  });
});
