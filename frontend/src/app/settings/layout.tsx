import type { ReactNode } from 'react'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const SETTINGS_MESSAGE_NAMESPACES = [
  'agent',
  'artifacts',
  'chat',
  'credentials',
  'marketplace',
  'model',
  'scheduleCenter',
  'shared',
  'systemCredentials',
  'systemLlm',
  'usage',
] as const

export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <ScopedIntlProvider namespaces={SETTINGS_MESSAGE_NAMESPACES}>{children}</ScopedIntlProvider>
  )
}
