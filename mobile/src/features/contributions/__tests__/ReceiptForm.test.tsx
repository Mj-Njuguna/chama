import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';

import { withStepUp } from '../../../auth/stepUp';
import type { RunWithStepUp } from '../../../auth/StepUpProvider';
import type { AuthedCall, CallResult } from '../../../auth/tokenManager';
import type { ChamaApi, Member, ReceiptIn, ReceiptOut } from '../../chamas/chamaApi';
import type { Deps } from '../../m2/deps';
import type { M2Api } from '../../m2/m2Api';
import { ReceiptForm } from '../ReceiptForm';

const member: Member = {
  membership_id: '22222222-2222-2222-2222-222222222222',
  user_id: '33333333-3333-3333-3333-333333333333',
  name: 'Wanjiru',
  role: 'member',
  status: 'active',
  member_code: 'K7M2QX',
  phone: null,
  joined_at: '2026-11-01T00:00:00Z',
};

const saved: ReceiptOut = {
  receipt_id: '44444444-4444-4444-4444-444444444444',
  channel: 'bank',
  reference: 'FTC26110042',
  amount_minor: 500_000,
  received_on: '2026-11-26',
  member_id: member.membership_id,
  note: '',
  recorded_by_member_id: '55555555-5555-5555-5555-555555555555',
  created_at: '2026-11-26T09:00:00Z',
};

/** The server asks for a fresh PIN on the first try, then accepts. */
function receiptServer(answers: CallResult<ReceiptOut>[]) {
  const bodies: ReceiptIn[] = [];
  const createReceipt =
    (body: ReceiptIn): AuthedCall<ReceiptOut> =>
    async () => {
      bodies.push(body);
      return answers[Math.min(bodies.length - 1, answers.length - 1)] as CallResult<ReceiptOut>;
    };
  return { createReceipt, bodies };
}

const stepUpRequired: CallResult<ReceiptOut> = {
  ok: false,
  status: 403,
  error: { kind: 'api', status: 403, code: 'step_up_required', factors: ['pin'] },
};

let client: QueryClient;

async function renderForm(
  api: Pick<ChamaApi, 'createReceipt'>,
  prompts: string[],
  answer: 'verified' | 'cancelled',
) {
  const runWithStepUp: RunWithStepUp = (call) =>
    withStepUp(
      () => call('chm_at_x'),
      async (factor) => {
        prompts.push(factor);
        return answer;
      },
    );
  const deps: Deps = {
    chamaApi: api as ChamaApi,
    m2: {} as M2Api,
    callAuthed: (call) => call('chm_at_x'),
    runWithStepUp,
    chamaId: 'chama-1',
    role: 'treasurer',
    myUserId: 'user-1',
  };
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  await render(
    <QueryClientProvider client={client}>
      <ReceiptForm
        deps={deps}
        members={[member]}
        visible
        onClose={() => undefined}
        nowMs={() => Date.parse('2026-11-26T09:00:00Z')}
      />
    </QueryClientProvider>,
  );
}

async function fill() {
  await fireEvent.changeText(screen.getByTestId('receipt-reference'), 'ftc 2611 0042');
  await fireEvent.changeText(screen.getByTestId('receipt-amount'), '5,000');
  await fireEvent.press(screen.getByTestId(`option-${member.membership_id}`));
  await fireEvent.press(screen.getByText('Record receipt'));
}

afterEach(() => client.clear());

describe('manual receipt with step-up', () => {
  it('prompts for the PIN when the server asks, then retries once and succeeds', async () => {
    const server = receiptServer([stepUpRequired, { ok: true, status: 201, data: saved }]);
    const prompts: string[] = [];
    await renderForm(server, prompts, 'verified');
    expect(screen.getByDisplayValue('2026-11-26')).toBeTruthy();
    await fill();
    expect(await screen.findByText('Receipt recorded.')).toBeTruthy();
    expect(prompts).toEqual(['pin']);
    expect(server.bodies).toHaveLength(2);
    expect(server.bodies[1]).toEqual({
      channel: 'bank',
      reference: 'ftc 2611 0042',
      amount_minor: 500_000,
      received_on: '2026-11-26',
      member_id: member.membership_id,
      note: '',
    });
  });

  it('does not retry when the official cancels the PIN prompt', async () => {
    const server = receiptServer([stepUpRequired, { ok: true, status: 201, data: saved }]);
    const prompts: string[] = [];
    await renderForm(server, prompts, 'cancelled');
    await fill();
    await screen.findByText('Record receipt');
    expect(server.bodies).toHaveLength(1);
    expect(screen.queryByText('Receipt recorded.')).toBeNull();
  });

  it('explains a duplicate reference', async () => {
    const server = receiptServer([
      {
        ok: false,
        status: 409,
        error: { kind: 'api', status: 409, code: 'state_conflict', reason: 'duplicate_receipt' },
      },
    ]);
    await renderForm(server, [], 'verified');
    await fill();
    expect(await screen.findByText('A receipt with this reference was already recorded.')).toBeTruthy();
  });
});
