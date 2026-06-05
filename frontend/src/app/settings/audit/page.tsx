import { SettingsShell } from '../_components/settings-shell'
import { AuditEventsContent } from './_components/audit-events-content'

export default function AuditSettingsPage() {
  return (
    <SettingsShell>
      <AuditEventsContent scope="mine" />
    </SettingsShell>
  )
}
