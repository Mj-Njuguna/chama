import type { ApiClient, Schemas } from '../../api/client';
import { bearer, runCall } from '../../auth/apiCall';
import type { AuthedCall } from '../../auth/tokenManager';

export type Me = Schemas['Me'];
export type MeMembership = Schemas['MeMembership'];
export type Role = Schemas['RoleEnum'];
export type CreatedChama = Schemas['CreatedChama'];
export type SwitchedChama = Schemas['SwitchedChama'];
export type ChamaHome = Schemas['ChamaHome'];
export type Member = Schemas['Member'];
export type ReceiptIn = Schemas['ReceiptInRequest'];
export type ReceiptOut = Schemas['ReceiptOut'];

/** Chama calls of the signed-in user; each takes the access token from the TokenManager. */
export type ChamaApi = {
  readonly me: AuthedCall<Me>;
  readonly create: (name: string) => AuthedCall<CreatedChama>;
  readonly switchTo: (chamaId: string) => AuthedCall<SwitchedChama>;
  readonly home: AuthedCall<ChamaHome>;
  readonly members: AuthedCall<{ members: Member[] }>;
  readonly createReceipt: (body: ReceiptIn) => AuthedCall<ReceiptOut>;
};

export function createChamaApi(client: ApiClient): ChamaApi {
  return {
    me: (token) => runCall(() => client.GET('/api/v1/me', { headers: bearer(token) })),
    create: (name) => (token) =>
      runCall(() => client.POST('/api/v1/chamas', { headers: bearer(token), body: { name } })),
    switchTo: (chamaId) => (token) =>
      runCall(() =>
        client.POST('/api/v1/auth/switch-chama', { headers: bearer(token), body: { chama_id: chamaId } }),
      ),
    home: (token) => runCall(() => client.GET('/api/v1/chama/home', { headers: bearer(token) })),
    members: (token) => runCall(() => client.GET('/api/v1/chama/members', { headers: bearer(token) })),
    createReceipt: (body) => (token) =>
      runCall(() => client.POST('/api/v1/chama/receipts', { headers: bearer(token), body })),
  };
}

/** Chair, treasurer and secretary see the official view. */
export function isOfficial(role: Role | null | undefined): boolean {
  return role === 'chair' || role === 'treasurer' || role === 'secretary';
}
