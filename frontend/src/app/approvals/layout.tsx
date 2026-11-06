import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const APPROVAL_MESSAGE_NAMESPACES = ['approvals', 'emergencyStop'] as const

export default function ApprovalsLayout({ children }: { children: ReactNode }) {
  return (
    <ScopedIntlProvider namespaces={APPROVAL_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
  )
}
