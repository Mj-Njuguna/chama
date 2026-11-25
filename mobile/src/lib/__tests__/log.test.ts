import { log, redact, redactValue, setLogSink } from '../log';

const ACCESS = 'chm_at_' + 'A'.repeat(43);
const REFRESH = 'chm_rt_' + 'b-_9'.repeat(10) + 'xyz';
const SECRETS = [ACCESS, REFRESH, '312080', '+254712345678', '0712 345 678', '254712345678', '0712-345-678'];

function assertClean(output: string) {
  for (const secret of SECRETS) {
    expect(output).not.toContain(secret);
  }
  // No digit run long enough to be a code or a phone number survives.
  expect(output).not.toMatch(/\d{4,}/);
}

describe('redact', () => {
  it.each([
    [`refresh ${REFRESH} failed`, 'refresh [token] failed'],
    [`Authorization: Bearer ${ACCESS}`, 'Authorization: Bearer [token]'],
    ['code 312080 sent', 'code [digits] sent'],
    ['phone +254712345678', 'phone [phone]'],
    ['phone 0712 345 678', 'phone [phone]'],
    ['phone (0712) 345-678', 'phone [phone]'],
    ['HTTP 401 after 3 tries', 'HTTP 401 after 3 tries'],
  ])('%p', (input, expected) => {
    expect(redact(input)).toBe(expected);
  });
});

describe('redactValue', () => {
  it('drops sensitive keys and redacts nested strings', () => {
    const value = redactValue({
      access_token: ACCESS,
      refresh_token: REFRESH,
      code: '312080',
      phone: '0712345678',
      device: { installation_id: 'install-demo-0001', platform: 'android' },
      error: { code: 'invalid_code', message: `bad ${REFRESH}` },
      attempts: [{ note: 'sent to +254712345678' }],
    });
    const output = JSON.stringify(value);
    assertClean(output);
    expect(output).not.toContain('install-demo-0001');
    expect(output).toContain('android');
  });

  it('renders errors as name and redacted message only', () => {
    expect(redactValue(new TypeError(`fetch failed for ${ACCESS}`))).toBe(
      'TypeError: fetch failed for [token]',
    );
  });
});

describe('log', () => {
  afterEach(() => setLogSink(null));

  it('redacts message and context before they reach the sink', () => {
    const lines: string[] = [];
    setLogSink((level, message, context) => lines.push(`${level} ${message} ${JSON.stringify(context)}`));

    log.info(`login ok for 0712345678 with ${ACCESS}`, { refresh_token: REFRESH, otp: '312080' });
    log.error('refresh failed', new Error(`token ${REFRESH} rejected`));
    log.debug('verify 312080');

    expect(lines).toHaveLength(3);
    assertClean(lines.join('\n'));
  });

  it('writes nothing when no sink is set (release builds)', () => {
    setLogSink(null);
    expect(() => log.warn('nothing')).not.toThrow();
  });
});
