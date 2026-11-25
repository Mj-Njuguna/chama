import { formatKes, formatMoney, InvalidMoneyAmount } from '../money';

describe('formatMoney', () => {
  it.each([
    [0, 'KES 0.00'],
    [1, 'KES 0.01'],
    [99, 'KES 0.99'],
    [100, 'KES 1.00'],
    [123456, 'KES 1,234.56'],
    [1500000, 'KES 15,000.00'],
    [100000000000, 'KES 1,000,000,000.00'],
    [-5000, '-KES 50.00'],
    [-1, '-KES 0.01'],
    [Number.MAX_SAFE_INTEGER, 'KES 90,071,992,547,409.91'],
  ])('%p minor units -> %p', (minor, expected) => {
    expect(formatMoney(minor)).toBe(expected);
  });

  it.each([12.5, 0.1, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1])(
    'refuses non-integer or unsafe amounts (%p)',
    (value) => {
      expect(() => formatMoney(value)).toThrow(InvalidMoneyAmount);
    },
  );
});

describe('formatKes (screens: whole shillings without cents)', () => {
  it.each([
    [0, 'KES 0'],
    [1200000, 'KES 12,000'],
    [1200050, 'KES 12,000.50'],
    [-250000, '-KES 2,500'],
  ])('%p minor units -> %p', (minor, expected) => {
    expect(formatKes(minor)).toBe(expected);
  });

  it('refuses non-integer amounts like formatMoney', () => {
    expect(() => formatKes(12.5)).toThrow(InvalidMoneyAmount);
  });
});
