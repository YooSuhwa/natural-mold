import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const WORKFLOW_MESSAGE_NAMESPACES = ['workflows'] as const

export default function WorkflowsLayout({ children }: { children: ReactNode }) {
  return (
    <ScopedIntlProvider namespaces={WORKFLOW_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
  )
}
