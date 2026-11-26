import createClient, { type Client } from 'openapi-fetch';

import type { components, paths } from './generated/draft-m2';

/**
 * DRAFT client for the phase-2 endpoints, typed from `src/api/draft/m2.openapi.yaml`
 * (`npm run gen:api:draft`). This is the ONLY module that imports the draft types. When the
 * backend schema publishes these endpoints, point these exports at `./generated/schema` and the
 * main client, then delete the draft files.
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
