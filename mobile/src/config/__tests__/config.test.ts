import { parseConfig } from '../config';

describe('parseConfig', () => {
  it('accepts the emulator dev URL in development builds', () => {
    expect(parseConfig('http://10.0.2.2:8000', true)).toEqual({
      ok: true,
      config: { apiBaseUrl: 'http://10.0.2.2:8000', isDev: true },
    });
  });

  it('strips trailing slashes and surrounding whitespace', () => {
    const result = parseConfig('  https://api.example.test/  ', false);
    expect(result).toEqual({ ok: true, config: { apiBaseUrl: 'https://api.example.test', isDev: false } });
  });

  it('refuses http in release builds', () => {
    expect(parseConfig('http://10.0.2.2:8000', false)).toEqual({ ok: false, error: 'insecure_api_base_url' });
  });

  it('accepts https in release builds', () => {
    expect(parseConfig('https://api.example.test:8443/base', false).ok).toBe(true);
  });

  it.each([undefined, '', '   '])('reports a missing URL (%p)', (raw) => {
    expect(parseConfig(raw, true)).toEqual({ ok: false, error: 'missing_api_base_url' });
  });

  it.each([
    '10.0.2.2:8000',
    'ftp://host',
    'http://',
    'http://user:pw@host',
    'http://host/?q=1',
    'http://host/#x',
    'http://ho st',
  ])('rejects a malformed URL (%p)', (raw) => {
    expect(parseConfig(raw, true)).toEqual({ ok: false, error: 'invalid_api_base_url' });
  });
});
