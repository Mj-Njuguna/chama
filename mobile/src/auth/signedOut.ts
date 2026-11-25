import type { MessageKey } from '../i18n';
import type { TerminalReason } from './tokenManager';

/** Why the login screen is shown again; drives a friendly, non-alarming explanation. */
export type SignedOutReason = TerminalReason | 'unreadable_credentials' | 'user_logout';

/**
 * refresh_reused is usually not theft but the grace-window sharp edge: a refresh answer was lost
 * on a flaky network and the app tried again too late. The message says "for your security"
 * without blaming the user or suggesting an attack (no special UI in M1, contract).
 */
export function signedOutMessageKey(reason: SignedOutReason): MessageKey | null {
  switch (reason) {
    case 'refresh_reused':
      return 'auth.signedOut.security';
    case 'session_revoked':
      return 'auth.signedOut.revoked';
    case 'session_expired':
      return 'auth.signedOut.expired';
    case 'invalid_token':
    case 'unreadable_credentials':
      return 'auth.signedOut.again';
    case 'user_logout':
      return null;
  }
}
