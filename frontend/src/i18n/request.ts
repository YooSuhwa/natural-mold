import { getRequestConfig } from 'next-intl/server'

export default getRequestConfig(async () => ({
  locale: 'ko',
  messages: (await import('../../messages/ko.json')).default,
}))
