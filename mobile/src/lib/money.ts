/**
 * The ONE money formatter. Amounts are integer minor units
 * (cents) end to end; this runs only at the display edge. No floating-point arithmetic: the
 * integer is split into shillings and cents with integer operations.
 */

export const CURRENCY = 'KES';

export class InvalidMoneyAmount extends Error {
  constructor(value: unknown) {
    super(`Money must be a safe integer of minor units, got ${typeof value}`);
    this.name = 'InvalidMoneyAmount';
  }
}

/** 123456 -> "KES 1,234.56"; -5000 -> "-KES 50.00"; 0 -> "KES 0.00". */
export function formatMoney(minor: number): string {
  if (!Number.isSafeInteger(minor)) {
    throw new InvalidMoneyAmount(minor);
  }
  const negative = minor < 0;
  const absolute = Math.abs(minor);
  const major = Math.trunc(absolute / 100);
  const cents = absolute % 100;
  const grouped = String(major).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '-' : ''}${CURRENCY} ${grouped}.${String(cents).padStart(2, '0')}`;
}

/**
 * Display form used by the signed-in screens: whole shillings without ".00", cents
 * only when there are some. 1500000 -> "KES 15,000"; 1500050 -> "KES 15,000.50". Same integer-only
 * rules as formatMoney.
 */
export function formatKes(minor: number): string {
  const full = formatMoney(minor);
  return full.endsWith('.00') ? full.slice(0, -3) : full;
}

/**
 * Parses what a person typed as an amount in shillings ("1500", "1,500", "1500.5", "1500.50")
 * into integer minor units, using string operations only (no floating point). Returns null for
 * anything else, zero, or more than two decimals.
 */
export function parseKesInput(text: string): number | null {
  const cleaned = text.replace(/[\s,]/g, '').replace(/^KES/i, '');
  const match = /^(\d{1,9})(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (match === null) return null;
  const shillings = Number(match[1]);
  const cents = Number((match[2] ?? '').padEnd(2, '0'));
  const minor = shillings * 100 + cents;
  return minor > 0 && Number.isSafeInteger(minor) ? minor : null;
}

/** Minor units as an editable input ("150000" -> "1500", "150050" -> "1500.50"). */
export function minorToInput(minor: number): string {
  if (!Number.isSafeInteger(minor) || minor <= 0) return '';
  const cents = minor % 100;
  const major = Math.trunc(minor / 100);
  return cents === 0 ? String(major) : `${major}.${String(cents).padStart(2, '0')}`;
}
