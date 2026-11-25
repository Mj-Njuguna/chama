import type { SignedOutReason } from './signedOut';

type Listener = (reason: SignedOutReason) => void;

/** Connects `Session.onSignedOut` (created before React) to the SessionProvider. */
export function createSignedOutChannel() {
  const listeners = new Set<Listener>();
  return {
    emit(reason: SignedOutReason): void {
      listeners.forEach((listener) => listener(reason));
    },
    subscribe(listener: Listener): () => void {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export type SignedOutChannel = ReturnType<typeof createSignedOutChannel>;
