export const locales = ['ru', 'en', 'es', 'de', 'fr', 'pt', 'tr', 'uk'] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = 'ru';

export const localeNames: Record<Locale, string> = {
  ru: 'Русский',
  en: 'English',
  es: 'Español',
  de: 'Deutsch',
  fr: 'Français',
  pt: 'Português',
  tr: 'Türkçe',
  uk: 'Українська',
};
