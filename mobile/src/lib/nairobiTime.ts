import { type MessageKey, t } from '../i18n';

/**
 * Display helpers for server timestamps. Business time is Africa/Nairobi,
 * which is UTC+3 all year (no daylight saving), so a fixed offset is exact and needs no Intl
 * time-zone data. These only FORMAT what the server sent; they never decide a status.
 */
const OFFSET_MS = 3 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

const SHORT_MONTHS: readonly MessageKey[] = [
  'month.short.1',
  'month.short.2',
  'month.short.3',
  'month.short.4',
  'month.short.5',
  'month.short.6',
  'month.short.7',
  'month.short.8',
  'month.short.9',
  'month.short.10',
  'month.short.11',
  'month.short.12',
];
const LONG_MONTHS: readonly MessageKey[] = [
  'month.long.1',
  'month.long.2',
  'month.long.3',
  'month.long.4',
  'month.long.5',
  'month.long.6',
  'month.long.7',
  'month.long.8',
  'month.long.9',
  'month.long.10',
  'month.long.11',
  'month.long.12',
];

type Parts = { year: number; month: number; day: number; hour: number; minute: number; dayNumber: number };

function partsOf(ms: number): Parts {
  const local = new Date(ms + OFFSET_MS);
  return {
    year: local.getUTCFullYear(),
    month: local.getUTCMonth() + 1,
    day: local.getUTCDate(),
    hour: local.getUTCHours(),
    minute: local.getUTCMinutes(),
    dayNumber: Math.floor((ms + OFFSET_MS) / DAY_MS),
  };
}

/** "YYYY-MM-DD" (a Nairobi calendar date) as the ms of its Nairobi midnight. */
function dateOnlyMs(date: string): number {
  return Date.parse(`${date}T00:00:00Z`) - OFFSET_MS;
}

const pad = (n: number) => String(n).padStart(2, '0');

function shortMonth(month: number): string {
  return t(SHORT_MONTHS[month - 1] ?? 'month.short.1');
}

/** "10:42" in Nairobi time. */
export function formatClock(iso: string): string {
  const p = partsOf(Date.parse(iso));
  return `${pad(p.hour)}:${pad(p.minute)}`;
}

/** "20 Nov" */
function dayMonth(p: Parts): string {
  return `${p.day} ${shortMonth(p.month)}`;
}

/** Group heading for a list of payments: "Today", "Yesterday" or "20 Nov". */
export function dayLabel(iso: string, nowMs: number): string {
  const diff = partsOf(nowMs).dayNumber - partsOf(Date.parse(iso)).dayNumber;
  if (diff === 0) return t('time.today');
  if (diff === 1) return t('time.yesterday');
  return dayMonth(partsOf(Date.parse(iso)));
}

/** "2 min ago", "1 h ago", "Yesterday" or "20 Nov" (latest payments on Home). */
export function relativeLabel(iso: string, nowMs: number): string {
  const ms = Date.parse(iso);
  const minutes = Math.max(0, Math.floor((nowMs - ms) / 60_000));
  const sameDay = partsOf(nowMs).dayNumber === partsOf(ms).dayNumber;
  if (minutes < 1) return t('time.justNow');
  if (minutes < 60) return t('time.minutesAgo', { count: minutes });
  if (sameDay) return t('time.hoursAgo', { count: Math.floor(minutes / 60) });
  return dayLabel(iso, nowMs);
}

/** "23 Nov 2026" for a Nairobi date ("YYYY-MM-DD"). */
export function formatDate(date: string): string {
  const p = partsOf(dateOnlyMs(date));
  return `${p.day} ${shortMonth(p.month)} ${p.year}`;
}

/** "Today, 23 Nov 2026" when the date is today in Nairobi, else "23 Nov 2026". */
export function formatDateWithToday(date: string, nowMs: number): string {
  return date === nairobiToday(nowMs)
    ? t('time.todayWithDate', { date: formatDate(date) })
    : formatDate(date);
}

/** "4 Oct" for a Nairobi date or a timestamp. */
export function formatDayMonth(dateOrIso: string): string {
  const ms = dateOrIso.length === 10 ? dateOnlyMs(dateOrIso) : Date.parse(dateOrIso);
  return dayMonth(partsOf(ms));
}

/** "November 2026" for a billing period ("YYYY-MM" or a date inside it). */
export function formatMonthYear(period: string): string {
  const [year, month] = period.split('-').map(Number);
  return `${t(LONG_MONTHS[(month ?? 1) - 1] ?? 'month.long.1')} ${year ?? ''}`;
}

/** Today's Nairobi date as "YYYY-MM-DD". */
export function nairobiToday(nowMs: number): string {
  const p = partsOf(nowMs);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

/** Device list: "Active now" (within 5 minutes), "Last active today/yesterday/N days ago". */
export function lastActiveLabel(iso: string, nowMs: number): string {
  const ms = Date.parse(iso);
  if (nowMs - ms < 5 * 60_000) return t('devices.activeNow');
  const days = partsOf(nowMs).dayNumber - partsOf(ms).dayNumber;
  if (days <= 0) return t('devices.activeToday');
  if (days === 1) return t('devices.activeYesterday');
  return t('devices.activeDaysAgo', { count: days });
}
