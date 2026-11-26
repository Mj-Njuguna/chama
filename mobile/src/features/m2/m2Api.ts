import type { M2Client, M2Schemas } from '../../api/m2Client';
import { bearer, runCall } from '../../auth/apiCall';
import type { AuthedCall } from '../../auth/tokenManager';

export type PaymentLinking = M2Schemas['LinkingOut'];
export type PaymentLinkingUpdate = M2Schemas['LinkingInRequest'];
export type StkRequest = M2Schemas['StkRequest'];
export type StkStatus = M2Schemas['StkStatusEnum'];
export type Payment = M2Schemas['Payment'];
export type UnallocatedPayment = M2Schemas['UnallocatedPayment'];
export type Cycle = M2Schemas['Cycle'];
export type Obligation = M2Schemas['Obligation'];
export type Collection = M2Schemas['Collection'];
export type CollectionMember = M2Schemas['CollectionRow'];
export type StatementEntry = M2Schemas['StatementEntry'];
export type Cashbook = M2Schemas['Cashbook'];
export type CashbookTransaction = M2Schemas['CashbookEntry'];
export type Waiver = M2Schemas['Waiver'];
export type FeedPage = M2Schemas['FeedPage'];
export type FeedEvent = M2Schemas['FeedEvent'];
export type MembershipPreferences = M2Schemas['MembershipPreferences'];

/** Phase-2 calls (payments, cycles, ledger views, waivers, feed). Each takes the access token. */
export type M2Api = {
  readonly paymentLinking: AuthedCall<PaymentLinking>;
  readonly updatePaymentLinking: (body: PaymentLinkingUpdate) => AuthedCall<PaymentLinking>;
  readonly createStk: (amountMinor: number) => AuthedCall<StkRequest>;
  readonly stkStatus: (stkRequestId: string) => AuthedCall<StkRequest>;
  readonly payments: (mine: boolean) => AuthedCall<{ payments: Payment[] }>;
  readonly unallocated: AuthedCall<{ payments: UnallocatedPayment[] }>;
  readonly allocate: (paymentId: string, memberId: string) => AuthedCall<Payment>;
  readonly currentCycle: AuthedCall<Cycle>;
  readonly obligations: (member: string) => AuthedCall<{ obligations: Obligation[] }>;
  readonly collection: AuthedCall<Collection>;
  readonly statement: AuthedCall<{ entries: StatementEntry[] }>;
  readonly cashbook: AuthedCall<Cashbook>;
  readonly requestWaiver: (obligationId: string, reason: string) => AuthedCall<Waiver>;
  readonly pendingWaivers: AuthedCall<{ waivers: Waiver[] }>;
  readonly approveWaiver: (waiverId: string) => AuthedCall<Waiver>;
  readonly feed: (cursor: string | null) => AuthedCall<FeedPage>;
  readonly membership: AuthedCall<MembershipPreferences>;
  readonly setScheduledStk: (optIn: boolean) => AuthedCall<MembershipPreferences>;
};

export function createM2Api(client: M2Client): M2Api {
  return {
    paymentLinking: (token) =>
      runCall(() => client.GET('/api/v1/chama/payment-linking', { headers: bearer(token) })),
    updatePaymentLinking: (body) => (token) =>
      runCall(() => client.PUT('/api/v1/chama/payment-linking', { headers: bearer(token), body })),
    createStk: (amountMinor) => (token) =>
      runCall(() =>
        client.POST('/api/v1/chama/payments/stk', {
          headers: bearer(token),
          body: { amount_minor: amountMinor },
        }),
      ),
    stkStatus: (id) => (token) =>
      runCall(() =>
        client.GET('/api/v1/chama/payments/stk/{stk_request_id}', {
          headers: bearer(token),
          params: { path: { stk_request_id: id } },
        }),
      ),
    payments: (mine) => (token) =>
      runCall(() =>
        client.GET('/api/v1/chama/payments', { headers: bearer(token), params: { query: { mine } } }),
      ),
    unallocated: (token) =>
      runCall(() => client.GET('/api/v1/chama/payments/unallocated', { headers: bearer(token) })),
    allocate: (paymentId, memberId) => (token) =>
      runCall(() =>
        client.POST('/api/v1/chama/payments/{payment_id}/allocate', {
          headers: bearer(token),
          params: { path: { payment_id: paymentId } },
          body: { member_id: memberId },
        }),
      ),
    currentCycle: (token) =>
      runCall(() => client.GET('/api/v1/chama/cycles/current', { headers: bearer(token) })),
    obligations: (member) => (token) =>
      runCall(() =>
        client.GET('/api/v1/chama/obligations', { headers: bearer(token), params: { query: { member } } }),
      ),
    collection: (token) => runCall(() => client.GET('/api/v1/chama/collection', { headers: bearer(token) })),
    statement: (token) => runCall(() => client.GET('/api/v1/chama/statement', { headers: bearer(token) })),
    cashbook: (token) => runCall(() => client.GET('/api/v1/chama/cashbook', { headers: bearer(token) })),
    requestWaiver: (obligationId, reason) => (token) =>
      runCall(() =>
        client.POST('/api/v1/chama/fines/{obligation_id}/waive', {
          headers: bearer(token),
          params: { path: { obligation_id: obligationId } },
          body: { reason },
        }),
      ),
    pendingWaivers: (token) =>
      runCall(() =>
        client.GET('/api/v1/chama/waivers', {
          headers: bearer(token),
          params: { query: { status: 'pending' } },
        }),
      ),
    approveWaiver: (waiverId) => (token) =>
      runCall(() =>
        client.POST('/api/v1/chama/waivers/{waiver_id}/approve', {
          headers: bearer(token),
          params: { path: { waiver_id: waiverId } },
        }),
      ),
    feed: (cursor) => (token) =>
      runCall(() =>
        client.GET('/api/v1/chama/feed', {
          headers: bearer(token),
          params: { query: cursor === null ? {} : { cursor } },
        }),
      ),
    membership: (token) => runCall(() => client.GET('/api/v1/chama/membership', { headers: bearer(token) })),
    setScheduledStk: (optIn) => (token) =>
      runCall(() =>
        client.PATCH('/api/v1/chama/membership', {
          headers: bearer(token),
          body: { scheduled_stk_opt_in: optIn },
        }),
      ),
  };
}
