import { getLocale, placeholdersOf, setLocale, t } from '..';
import { en, type MessageKey } from '../en';
import { sw } from '../sw';

afterEach(() => setLocale('en'));

describe('t', () => {
  it('returns the English message', () => {
    expect(t('app.name')).toBe('Chama');
  });

  it('interpolates named placeholders', () => {
    expect(t('time.minutesAgo', { count: 5 })).toBe('5 min ago');
  });

  it('leaves unknown placeholders visible rather than dropping them', () => {
    expect(t('time.minutesAgo', {})).toBe('{count} min ago');
  });

  it('falls back to English for keys Swahili does not have yet', () => {
    setLocale('sw');
    expect(getLocale()).toBe('sw');
    expect(t('app.name')).toBe(en['app.name']);
  });
});

describe('catalogs', () => {
  it('Swahili translations use exactly the placeholders of the English message', () => {
    for (const [key, message] of Object.entries(sw) as [MessageKey, string][]) {
      expect([key, placeholdersOf(message)]).toEqual([key, placeholdersOf(en[key])]);
    }
  });

  it('has no empty English messages', () => {
    for (const [key, message] of Object.entries(en)) {
      expect([key, message.trim().length > 0]).toEqual([key, true]);
    }
  });
});
