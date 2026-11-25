import { createApiClient } from '../client';

// The client is exercised with an injected fetch (no real network). Shapes come from the
// generated schema, so a request body that does not match OtpRequestRequest fails typecheck.
describe('createApiClient', () => {
  it('posts typed JSON to the base URL + contract path', async () => {
    const fetchImpl = jest.fn(async (_request: Request) =>
      Response.json(
        {
          challenge_id: 'ab7c33d8-fc31-4b5f-8f48-624fdc497f53',
          channel: 'whatsapp',
          expires_in: 300,
          resend_after: 60,
        },
        { status: 202 },
      ),
    );
    const client = createApiClient('http://10.0.2.2:8000', fetchImpl as unknown as typeof fetch);

    const { data, error, response } = await client.POST('/api/v1/auth/otp/request', {
      body: { phone: '+254712345678', device_id: 'install-demo-0001' },
    });

    expect(response.status).toBe(202);
    expect(error).toBeUndefined();
    expect(data?.channel).toBe('whatsapp');
    const request = fetchImpl.mock.calls[0]?.[0];
    expect(request?.url).toBe('http://10.0.2.2:8000/api/v1/auth/otp/request');
    expect(request?.method).toBe('POST');
    expect(await request?.json()).toEqual({ phone: '+254712345678', device_id: 'install-demo-0001' });
  });
});
