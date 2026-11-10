import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const MCP_MESSAGE_NAMESPACES = [
  'credentials',
  'emergencyStop',
  'marketplace',
  'mcp',
  // The registrant's notice and badge for a server waiting for an administrator.
  'registration',
  'shared',
] as const

export default function McpServersLayout({ children }: { children: ReactNode }) {
  return <ScopedIntlProvider namespaces={MCP_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
}
