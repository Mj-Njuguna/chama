import { formatWait, loginErrorView } from '../messages';

describe('formatWait rounds up, never too early', () => {
  it.each([
    [0.2, '1 second'],
    [1, '1 second'],
    [56, '56 seconds'],
    [60, '1 minute'],
    [61, '2 minutes'],
    [900, '15 minutes'],
    [3600, '1 hour'],
    [86400, '24 hours'],
  ])('%s s -> %s', (seconds, text) => {
    expect(formatWait(seconds)).toBe(text);
  });
});

describe('loginErrorView', () => {
  it('never shows the server message, only the mapped text', () => {
    const view = loginErrorView({ kind: 'api', status: 500, code: 'unrecognized_error' });
    expect(view.message).toBe('Something went wrong. Please try again.');
  });

  it('rate_limited without retry_after still explains, with no wait', () => {
    expect(loginErrorView({ kind: 'api', status: 429, code: 'rate_limited' })).toEqual({
      message: 'Please wait a little before trying again.',
    });
  });

  it('service_unavailable offers a retry', () => {
    expect(loginErrorView({ kind: 'api', status: 503, code: 'service_unavailable' }).message).toMatch(
      /try again/i,
    );
  });
});
