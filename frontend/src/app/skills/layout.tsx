import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const SKILL_MESSAGE_NAMESPACES = ['credentials', 'marketplace', 'skill'] as const

export default function SkillsLayout({ children }: { children: ReactNode }) {
  return <ScopedIntlProvider namespaces={SKILL_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
}
