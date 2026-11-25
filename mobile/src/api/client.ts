import createClient, { type Client } from 'openapi-fetch';

import type { components, paths } from './generated/schema';

/**
 * Typed API client over GENERATED schema types (`npm run gen:api` from the backend OpenAPI
 * schema). Request and response types come only from the generated file; never hand-write them.
 */
export type ApiClient = Client<paths>;
export type Schemas = components['schemas'];

export function createApiClient(apiBaseUrl: string, fetchImpl?: typeof fetch): ApiClient {
  return createClient<paths>({
    baseUrl: apiBaseUrl,
    headers: { Accept: 'application/json' },
    ...(fetchImpl === undefined ? {} : { fetch: fetchImpl }),
  });
}
