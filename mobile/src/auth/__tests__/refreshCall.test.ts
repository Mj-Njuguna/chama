import { createApiClient } from '../../api/client';
import { makeRefreshCall } from '../refreshCall';

const BASE = 'http://10.0.2.2:8000';

function clientAnswering(respond: (request: Request) => Promise<Response>) {
  const fetchImpl = jest.fn(respond);
  return { call: makeRefreshCall(createApiClient(BASE, fetchImpl as unknown as typeof fetch)), fetchImpl };
}

const pair = {
  session_id: 'abbe778d-812f-4251-bc99-1934ee33aebb',
  access_token: 'chm_at_new',
  access_expires_in: 900,
  refresh_token: 'chm_rt_new',
  refresh_expires_in: 2592000,
};

describe('makeRefreshCall', () => {
  it('posts the refresh token without an Authorization header and returns the pair', async () => {
    const { call, fetchImpl } = clientAnswering(async () => Response.json(pair));
    await expect(call('chm_rt_old')).resolves.toEqual({ kind: 'ok', pair });
    const request = fetchImpl.mock.calls[0]![0];
    expect(request.url).toBe(`${BASE}/api/v1/auth/token/refresh`);
    expect(request.headers.get('Authorization')).toBeNull();
    expect(await request.json()).toEqual({ refresh_token: 'chm_rt_old' });
  });

  it('maps a contract error', async () => {
    const { call } = clientAnswering(async () =>
      Response.json({ error: { code: 'refresh_reused', message: 'x' } }, { status: 401 }),
    );
    await expect(call('chm_rt_old')).resolves.toEqual({
      kind: 'api',
      error: { kind: 'api', status: 401, code: 'refresh_reused' },
    });
  });

  it('treats a lost response as network (retry with the same token is safe)', async () => {
    const { call } = clientAnswering(async () => {
      throw new TypeError('Network request failed');
    });
    await expect(call('chm_rt_old')).resolves.toEqual({ kind: 'network' });
  });
});
