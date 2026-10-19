import { OrganizationSettingsContent } from '@/features/organizations/components/organization-settings-content'
import { SettingsShell } from '../_components/settings-shell'

export default function OrganizationSettingsPage() {
  return (
    <SettingsShell>
      <OrganizationSettingsContent />
    </SettingsShell>
  )
}
