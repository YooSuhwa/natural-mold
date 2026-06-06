import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const SHARED_MESSAGE_NAMESPACES = ['chat', 'sharedConversation'] as const

export default function SharedLayout({ children }: { children: ReactNode }) {
  return <ScopedIntlProvider namespaces={SHARED_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
}
