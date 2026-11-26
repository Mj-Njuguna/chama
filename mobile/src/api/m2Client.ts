import createClient, { type Client } from 'openapi-fetch';

import type { components, paths } from './generated/schema';

/**
 * Typed client for the payments, cycles, ledger-view, waiver and feed endpoints, generated from the
 * backend schema (`npm run gen:api`).
 */
export type M2Client = Client<paths>;
export type M2Schemas = components['schemas'];

export function createM2Client(apiBaseUrl: string, fetchImpl?: typeof fetch): M2Client {
  return createClient<paths>({
    baseUrl: apiBaseUrl,
    headers: { Accept: 'application/json' },
    ...(fetchImpl === undefined ? {} : { fetch: fetchImpl }),
  });
}
