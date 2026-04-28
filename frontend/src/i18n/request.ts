import { getRequestConfig } from 'next-intl/server'

export default getRequestConfig(async () => ({
  locale: 'ko',
  timeZone: 'Asia/Seoul',
  messages: (await import('../../messages/ko.json')).default,
}))
