export const LOCALE_COOKIE_NAME = 'moldy_locale'
export const DEFAULT_LOCALE = 'ko'
export const SUPPORTED_LOCALES = ['ko', 'en'] as const

export type AppLocale = (typeof SUPPORTED_LOCALES)[number]

export function isSupportedLocale(value: string | undefined): value is AppLocale {
  return SUPPORTED_LOCALES.some((locale) => locale === value)
}
