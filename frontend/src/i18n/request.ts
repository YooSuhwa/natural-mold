import { cookies } from 'next/headers'
import { getRequestConfig } from 'next-intl/server'

import { DEFAULT_LOCALE, LOCALE_COOKIE_NAME, type AppLocale, isSupportedLocale } from './locales'

type Messages = Record<string, unknown>

const messageLoaders: Record<AppLocale, () => Promise<{ default: Messages }>> = {
  ko: () => import('../../messages/ko.json'),
  en: () => import('../../messages/en.json'),
}

function mergeMessages(base: Messages, overrides: Messages): Messages {
  const merged: Messages = { ...base }

  for (const [key, value] of Object.entries(overrides)) {
    const baseValue = merged[key]
    if (
      baseValue &&
      typeof baseValue === 'object' &&
      !Array.isArray(baseValue) &&
      value &&
      typeof value === 'object' &&
      !Array.isArray(value)
    ) {
      merged[key] = mergeMessages(baseValue as Messages, value as Messages)
      continue
    }

    merged[key] = value
  }

  return merged
}

export default getRequestConfig(async () => {
  const cookieStore = await cookies()
  const cookieLocale = cookieStore.get(LOCALE_COOKIE_NAME)?.value
  const locale = isSupportedLocale(cookieLocale) ? cookieLocale : DEFAULT_LOCALE

  const defaultMessages = (await messageLoaders[DEFAULT_LOCALE]()).default
  const messages =
    locale === DEFAULT_LOCALE
      ? defaultMessages
      : mergeMessages(defaultMessages, (await messageLoaders[locale]()).default)

  return {
    locale,
    timeZone: 'Asia/Seoul',
    messages,
  }
})
