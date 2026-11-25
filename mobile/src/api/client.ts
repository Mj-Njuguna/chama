import createClient, { type Client } from 'openapi-fetch';

import type { components, paths } from './generated/draft-chama';

/**
 * Typed API client over GENERATED schema types. Request and response types come only from the
 * generated file; never hand-write them.
 *
 * DRAFT: until the backend publishes these endpoints, types come from
 * `src/api/draft/chama.openapi.yaml` (`npm run gen:api:draft`). When the backend schema lands,
 * this import switches to `./generated/schema` (`npm run gen:api`) and the draft is deleted.
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
