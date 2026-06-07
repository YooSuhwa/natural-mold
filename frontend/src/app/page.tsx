import DashboardPage from './dashboard-page-client'
import { ScopedIntlProvider } from '@/i18n/scoped-messages'

const DASHBOARD_MESSAGE_NAMESPACES = ['agent', 'dashboard'] as const

export default function DashboardRoute() {
  return (
    <ScopedIntlProvider namespaces={DASHBOARD_MESSAGE_NAMESPACES}>
      <DashboardPage />
    </ScopedIntlProvider>
  )
}
