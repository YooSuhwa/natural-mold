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
  // The agent guardrail card shares rule, action and stage names with company settings.
  'guardrails',
] as const

export default function AgentsLayout({ children }: { children: ReactNode }) {
  return <ScopedIntlProvider namespaces={AGENT_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
}
