/** Milliseconds from a monotonic source. Only differences between two readings mean anything. */
export type MonotonicClock = () => number;

/**
 * The idle re-lock clock. Never the wall clock: changing the phone's date or time zone must not
 * extend (or shorten) the re-lock window.
 */
export function createMonotonicClock(): MonotonicClock {
  return () => performance.now();
}
