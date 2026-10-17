import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

// `credentials`/`shared`: the S23 connection dialog creates a credential in place (G-04).
const AGENT_MESSAGE_NAMESPACES = [
  'agent',
  'chat',
  'model',
  'skill',
  'usage',
  'credentials',
  'shared',
] as const

export default function AgentsLayout({ children }: { children: ReactNode }) {
  return <ScopedIntlProvider namespaces={AGENT_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
}
