import { LOCALE_COOKIE_NAME } from './locales'

const LOCALE_COOKIE_MAX_AGE_SECONDS = 31_536_000

export function persistLocaleCookie(locale: string) {
  if (typeof document === 'undefined') return
  document.cookie = `${LOCALE_COOKIE_NAME}=${encodeURIComponent(locale)}; path=/; max-age=${LOCALE_COOKIE_MAX_AGE_SECONDS}; SameSite=Lax`
}
