/**
 * The only logger. Everything is redacted before it leaves this module: tokens, bearer
 * headers, one-time codes and phone numbers never reach the console or a future crash reporter.
 * Release builds write nothing (no crash reporter is wired yet; when one is, it must call
 * `redact` / `redactValue` on everything it sends).
 *
 * Still: do not pass secrets here on purpose. Redaction is the safety net, not the plan.
 */

type Level = 'debug' | 'info' | 'warn' | 'error';

const SENSITIVE_KEY = /token|authorization|code|otp|pin|phone|msisdn|password|secret|cookie|installation/i;

const PATTERNS: readonly [RegExp, string][] = [
  // Opaque tokens from the backend: chm_at_… / chm_rt_…
  [/chm_(?:at|rt)_[A-Za-z0-9_-]+/g, '[token]'],
  [/(Bearer\s+)\S+/gi, '$1[token]'],
  // Phone numbers in any accepted format: 9+ digits, optionally with +, spaces, dots, dashes, parentheses.
  [/\+?\(?\d(?:[\s().-]*\d){8,}/g, '[phone]'],
  // One-time codes: any standalone 4-8 digit run.
  [/(?<![\d])\d{4,8}(?![\d])/g, '[digits]'],
];

export function redact(text: string): string {
  return PATTERNS.reduce((acc, [pattern, replacement]) => acc.replace(pattern, replacement), text);
}

/** Deep-redacts a value for logging: sensitive keys are dropped, strings are pattern-redacted. */
export function redactValue(value: unknown, depth = 0): unknown {
  if (depth > 5) return '[depth]';
  if (typeof value === 'string') return redact(value);
  if (typeof value === 'number') return redact(String(value));
  if (value instanceof Error) return `${value.name}: ${redact(value.message)}`;
  if (Array.isArray(value)) return value.map((item) => redactValue(item, depth + 1));
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        SENSITIVE_KEY.test(key) ? '[redacted]' : redactValue(item, depth + 1),
      ]),
    );
  }
  return value;
}

type Sink = (level: Level, message: string, context?: unknown) => void;

/* eslint-disable no-console -- this module is the single console boundary */
const consoleSink: Sink = (level, message, context) => {
  const method = level === 'debug' ? console.log : console[level];
  if (context === undefined) method(message);
  else method(message, context);
};
/* eslint-enable no-console */

let sink: Sink | null = __DEV__ ? consoleSink : null;

/** Tests only: capture output. */
export function setLogSink(next: Sink | null): void {
  sink = next;
}

function write(level: Level, message: string, context?: unknown): void {
  if (sink === null) return;
  sink(level, redact(message), context === undefined ? undefined : redactValue(context));
}

export const log = {
  debug: (message: string, context?: unknown) => write('debug', message, context),
  info: (message: string, context?: unknown) => write('info', message, context),
  warn: (message: string, context?: unknown) => write('warn', message, context),
  error: (message: string, context?: unknown) => write('error', message, context),
};
