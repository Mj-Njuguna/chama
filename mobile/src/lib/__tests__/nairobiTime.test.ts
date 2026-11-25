import {
  dayLabel,
  formatClock,
  formatDate,
  formatDateWithToday,
  formatDayMonth,
  formatMonthYear,
  lastActiveLabel,
  nairobiToday,
  relativeLabel,
} from '../nairobiTime';

// 23 Nov 2026, 11:00 in Nairobi (08:00 UTC).
const NOW = Date.parse('2026-11-23T08:00:00Z');

describe('Nairobi display time (UTC+3, no daylight saving)', () => {
  it('formats the clock in Nairobi time', () => {
    expect(formatClock('2026-11-23T07:42:00Z')).toBe('10:42');
    expect(formatClock('2026-11-22T21:30:00Z')).toBe('00:30');
  });

  it('uses the Nairobi calendar day for Today and Yesterday (not UTC)', () => {
    // 22 Nov 21:30 UTC is already 23 Nov 00:30 in Nairobi.
    expect(dayLabel('2026-11-22T21:30:00Z', NOW)).toBe('Today');
    expect(dayLabel('2026-11-22T20:59:00Z', NOW)).toBe('Yesterday');
    expect(dayLabel('2026-11-20T10:20:00Z', NOW)).toBe('20 Nov');
  });

  it('gives short relative times for the latest payments', () => {
    expect(relativeLabel('2026-11-23T07:59:40Z', NOW)).toBe('Just now');
    expect(relativeLabel('2026-11-23T07:58:00Z', NOW)).toBe('2 min ago');
    expect(relativeLabel('2026-11-23T06:55:00Z', NOW)).toBe('1 h ago');
    expect(relativeLabel('2026-11-22T14:05:00Z', NOW)).toBe('Yesterday');
  });

  it('formats dates and months', () => {
    expect(nairobiToday(NOW)).toBe('2026-11-23');
    expect(formatDate('2026-11-23')).toBe('23 Nov 2026');
    expect(formatDateWithToday('2026-11-23', NOW)).toBe('Today, 23 Nov 2026');
    expect(formatDateWithToday('2026-11-20', NOW)).toBe('20 Nov 2026');
    expect(formatDayMonth('2026-10-04')).toBe('4 Oct');
    expect(formatMonthYear('2026-11')).toBe('November 2026');
  });

  it('labels device activity', () => {
    expect(lastActiveLabel('2026-11-23T07:58:00Z', NOW)).toBe('Active now');
    expect(lastActiveLabel('2026-11-23T03:00:00Z', NOW)).toBe('Last active today');
    expect(lastActiveLabel('2026-11-20T08:00:00Z', NOW)).toBe('Last active 3 days ago');
  });
});
