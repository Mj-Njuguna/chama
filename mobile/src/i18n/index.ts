import { en, type MessageKey } from './en';
import { sw } from './sw';

export type { MessageKey } from './en';
export type Locale = 'en' | 'sw';
export type MessageParams = Readonly<Record<string, string | number>>;

const catalogs: Readonly<Record<Locale, Partial<Record<MessageKey, string>>>> = { en, sw };

let current: Locale = 'en';

export function setLocale(locale: Locale): void {
  current = locale;
}

export function getLocale(): Locale {
  return current;
}

/** Translate `key` in the current locale, falling back to English; `{name}` is replaced from params. */
export function t(key: MessageKey, params?: MessageParams): string {
  const template = catalogs[current][key] ?? en[key];
  if (params === undefined) {
    return template;
  }
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = params[name];
    return value === undefined ? whole : String(value);
  });
}

/** Placeholder names in a message, for catalog consistency tests. */
export function placeholdersOf(message: string): string[] {
  return [...message.matchAll(/\{(\w+)\}/g)].map((m) => m[1] ?? '').sort();
}
