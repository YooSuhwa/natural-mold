import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const TOOL_MESSAGE_NAMESPACES = ['credentials', 'shared', 'tool'] as const

export default function ToolsLayout({ children }: { children: ReactNode }) {
  return <ScopedIntlProvider namespaces={TOOL_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
}
