/**
 * Phone normalization to E.164, mirroring backend `core/phones.py` exactly. Both sides are pinned
 * by `backend/tests/data/phone-vectors.json`. libphonenumber-js is pinned to an exact
 * version: re-run the vector test on every upgrade (metadata changes move numbering ranges).
 *
 * The server stays authoritative (it normalizes again); this gives instant field errors.
 */
import { type CountryCode, ParseError, parsePhoneNumberWithError } from 'libphonenumber-js/max';

export type PhoneError = 'invalid_format' | 'invalid_number' | 'country_not_allowed' | 'not_mobile';

export type PhoneResult =
  { readonly ok: true; readonly e164: string } | { readonly ok: false; readonly error: PhoneError };

/** MVP allowlist; the backend setting is ALLOWED_COUNTRY_CODES. The first entry reads numbers without "+". */
export const DEFAULT_ALLOWED_COUNTRIES: readonly CountryCode[] = ['KE'];

const MAX_INPUT_LENGTH = 32;
const ALLOWED_CHARS = /^[0-9+\s().-]+$/;

// libphonenumber-js reports lengths as parse errors; Python's phonenumbers parses them and then
// fails validation. Both mean "a number, but not a valid one".
const LENGTH_ERRORS = new Set(['TOO_SHORT', 'TOO_LONG', 'INVALID_LENGTH']);

export function normalizePhone(
  raw: string,
  allowedCountries: readonly CountryCode[] = DEFAULT_ALLOWED_COUNTRIES,
): PhoneResult {
  const defaultCountry = allowedCountries[0];
  if (defaultCountry === undefined) {
    return { ok: false, error: 'country_not_allowed' };
  }

  const text = raw.trim();
  if (
    text === '' ||
    text.length > MAX_INPUT_LENGTH ||
    !ALLOWED_CHARS.test(text) ||
    text.slice(1).includes('+')
  ) {
    return { ok: false, error: 'invalid_format' };
  }

  let number;
  try {
    number = parsePhoneNumberWithError(text, { defaultCountry });
  } catch (error) {
    if (error instanceof ParseError && LENGTH_ERRORS.has(error.message)) {
      return { ok: false, error: 'invalid_number' };
    }
    return { ok: false, error: 'invalid_format' };
  }

  if (!number.isValid()) {
    return { ok: false, error: 'invalid_number' };
  }
  if (number.country === undefined || !allowedCountries.includes(number.country)) {
    return { ok: false, error: 'country_not_allowed' };
  }
  if (number.getType() !== 'MOBILE') {
    return { ok: false, error: 'not_mobile' };
  }
  return { ok: true, e164: number.number };
}

/**
 * Masked form for display and logs: country code and last 3 digits only ("+254 ••• ••• 678").
 * The full number is never shown on the shell or written to logs.
 */
export function maskPhone(e164: string): string {
  if (!/^\+\d{8,15}$/.test(e164)) return '•••';
  const last3 = e164.slice(-3);
  // Kenya is the only allowed country in the MVP; elsewhere, hide the country code too.
  return e164.startsWith('+254') ? `+254 ••• ••• ${last3}` : `+••• ••• ${last3}`;
}

/**
 * Local masked form for the Profile screen ("+254722140118" -> "0722 ••• 118"): the user's own
 * number, still never shown in full on a screen anyone can glance at.
 */
export function maskPhoneLocal(e164: string): string {
  if (!/^\+254\d{9}$/.test(e164)) return maskPhone(e164);
  const local = `0${e164.slice(4)}`;
  return `${local.slice(0, 4)} ••• ${local.slice(-3)}`;
}
