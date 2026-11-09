import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

// `workflows`: a workflow's "ask" step is answered right in the inbox (workflow-nodes-plan N5).
const APPROVAL_MESSAGE_NAMESPACES = ['approvals', 'emergencyStop', 'workflows'] as const

export default function ApprovalsLayout({ children }: { children: ReactNode }) {
  return (
    <ScopedIntlProvider namespaces={APPROVAL_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
  )
}
