import type { ReactNode } from 'react'
import { NextIntlClientProvider } from 'next-intl'
import { getMessages, getTimeZone } from 'next-intl/server'

type Messages = Record<string, unknown>

export const ROOT_MESSAGE_NAMESPACES = [
  'appSettings',
  'auth',
  'common',
  'metadata',
  'nav',
  'sidebar',
] as const

export type MessageNamespace = string

export function pickMessageNamespaces(
  messages: Messages,
  namespaces: readonly MessageNamespace[],
): Messages {
  const picked: Messages = {}

  for (const namespace of namespaces) {
    const value = messages[namespace]
    if (value !== undefined) {
      picked[namespace] = value
    }
  }

  return picked
}

export async function getScopedMessages(
  namespaces: readonly MessageNamespace[],
): Promise<Messages> {
  const messages = await getMessages()
  return pickMessageNamespaces(messages, namespaces)
}

export async function ScopedIntlProvider({
  children,
  namespaces,
}: {
  children: ReactNode
  namespaces: readonly MessageNamespace[]
}) {
  const scopedNamespaces = Array.from(new Set([...ROOT_MESSAGE_NAMESPACES, ...namespaces]))
  const [messages, timeZone] = await Promise.all([
    getScopedMessages(scopedNamespaces),
    getTimeZone(),
  ])

  return (
    <NextIntlClientProvider messages={messages} timeZone={timeZone}>
      {children}
    </NextIntlClientProvider>
  )
}
