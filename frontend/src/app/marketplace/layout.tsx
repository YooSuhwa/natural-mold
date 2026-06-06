import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const MARKETPLACE_MESSAGE_NAMESPACES = ['marketplace'] as const

export default function MarketplaceLayout({ children }: { children: ReactNode }) {
  return (
    <ScopedIntlProvider namespaces={MARKETPLACE_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
  )
}
