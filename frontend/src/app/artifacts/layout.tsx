import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const ARTIFACT_MESSAGE_NAMESPACES = ['artifacts', 'chat'] as const

export default function ArtifactsLayout({ children }: { children: ReactNode }) {
  return (
    <ScopedIntlProvider namespaces={ARTIFACT_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
  )
}
