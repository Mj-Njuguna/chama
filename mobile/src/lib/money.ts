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
 * Display form used by the signed-in screens (design 3a): whole shillings without ".00", cents
 * only when there are some. 1500000 -> "KES 15,000"; 1500050 -> "KES 15,000.50". Same integer-only
 * rules as formatMoney.
 */
export function formatKes(minor: number): string {
  const full = formatMoney(minor);
  return full.endsWith('.00') ? full.slice(0, -3) : full;
}
