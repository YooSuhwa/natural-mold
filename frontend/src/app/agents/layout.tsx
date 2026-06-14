import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const AGENT_MESSAGE_NAMESPACES = ['agent', 'chat', 'model', 'skill', 'usage'] as const

export default function AgentsLayout({ children }: { children: ReactNode }) {
  return <ScopedIntlProvider namespaces={AGENT_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
}
