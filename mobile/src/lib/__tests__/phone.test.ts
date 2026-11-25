import type { CountryCode } from 'libphonenumber-js/max';

import vectorsFile from '../../../../backend/tests/data/phone-vectors.json';
import { maskPhone, maskPhoneLocal, normalizePhone } from '../phone';

type Vector = {
  input: string;
  expected?: string;
  error?: string;
  allowed_countries?: CountryCode[];
  note: string;
};

const vectors = vectorsFile.vectors as Vector[];
const defaults = vectorsFile.default_allowed_countries as CountryCode[];

describe('normalizePhone matches the backend contract', () => {
  it('has vectors to check', () => {
    expect(vectors.length).toBeGreaterThan(50);
  });

  it.each(vectors.map((v) => [v.note, v] as const))('%s', (_note, v) => {
    const result = normalizePhone(v.input, v.allowed_countries ?? defaults);
    const expected =
      v.expected !== undefined ? { ok: true, e164: v.expected } : { ok: false, error: v.error };
    expect(result).toEqual(expected);
  });

  it('refuses everything when no country is allowed', () => {
    expect(normalizePhone('0712345678', [])).toEqual({ ok: false, error: 'country_not_allowed' });
  });
});

describe('maskPhone', () => {
  it('keeps only the country code and the last three digits', () => {
    expect(maskPhone('+254712345678')).toBe('+254 ••• ••• 678');
  });

  it('never echoes malformed input', () => {
    expect(maskPhone('0712345678')).toBe('•••');
  });
});

describe('maskPhoneLocal', () => {
  it('shows a Kenyan number in local form with the middle hidden', () => {
    expect(maskPhoneLocal('+254722140118')).toBe('0722 \u2022\u2022\u2022 118');
  });

  it('falls back to the international mask elsewhere', () => {
    expect(maskPhoneLocal('+14155550123')).toBe(maskPhone('+14155550123'));
  });
});
