/**
 * DEV ONLY: reads the newest code for a number from the backend's fake outbox
 * (`GET /dev/otp-outbox`). It is deliberately NOT in the OpenAPI
 * schema, so the response is checked at runtime. Production returns 404 for it.
 * Only reached behind `__DEV__`.
 */
export const DEV_OTP_OUTBOX_MARKER = 'DEV_ONLY_OTP_OUTBOX__NEVER_IN_RELEASE';

const CODE = /^\d{6}$/;

/** Returns the newest code, or null when there is none or the answer has an unexpected shape. */
export function newestCode(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null;
  const messages = (body as { messages?: unknown }).messages;
  if (!Array.isArray(messages) || messages.length === 0) return null;
  const first: unknown = messages[0];
  if (typeof first !== 'object' || first === null) return null;
  const code = (first as { code?: unknown }).code;
  return typeof code === 'string' && CODE.test(code) ? code : null;
}

export async function readOutboxCode(
  apiBaseUrl: string,
  phoneE164: string,
  fetchImpl: typeof fetch = fetch,
): Promise<string | null> {
  try {
    const response = await fetchImpl(`${apiBaseUrl}/dev/otp-outbox?phone=${encodeURIComponent(phoneE164)}`);
    if (!response.ok) return null;
    return newestCode(await response.json());
  } catch {
    return null;
  }
}
