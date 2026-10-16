import { OrgRolesContent } from '@/features/organizations/components/org-roles-content'
import { SettingsShell } from '../../_components/settings-shell'

export default function OrganizationRolesPage() {
  return (
    <SettingsShell>
      <OrgRolesContent />
    </SettingsShell>
  )
}
